import {normalizePublicationDesign, PUBLICATION_BINDINGS} from './publication-model.mjs';
import {renderPublicationPage} from './publication-render.mjs';

const dceClone=value=>JSON.parse(JSON.stringify(value));
const dceFinite=(value,fallback=0)=>Number.isFinite(Number(value))?Number(value):fallback;
const dceClamp=(value,min,max)=>Math.max(min,Math.min(max,value));

// Pointer movement is converted from the displayed page into its export units.
export function designCanvasDelta(start,current,bounds,surface){
  const width=dceFinite(bounds?.width),height=dceFinite(bounds?.height);
  if(width<=0||height<=0)return {x:0,y:0};
  return {x:(dceFinite(current?.x)-dceFinite(start?.x))*dceFinite(surface?.width)/width,
    y:(dceFinite(current?.y)-dceFinite(start?.y))*dceFinite(surface?.height)/height};
}

export function moveDesignCanvasNodes(surface,ids,delta,{snap=0}={}){
  const next=dceClone(surface),wanted=new Set(ids),nodes=next.nodes.filter(node=>wanted.has(node.id)&&!node.locked);
  if(!nodes.length)return next;
  const minX=Math.min(...nodes.map(n=>n.x)),minY=Math.min(...nodes.map(n=>n.y));
  const maxX=Math.max(...nodes.map(n=>n.x+n.w)),maxY=Math.max(...nodes.map(n=>n.y+n.h));
  let dx=dceFinite(delta.x),dy=dceFinite(delta.y);
  if(snap>0){dx=Math.round((minX+dx)/snap)*snap-minX;dy=Math.round((minY+dy)/snap)*snap-minY;}
  dx=dceClamp(dx,-minX,Math.max(-minX,next.width-maxX));dy=dceClamp(dy,-minY,Math.max(-minY,next.height-maxY));
  for(const node of nodes){node.x+=dx;node.y+=dy;}
  return next;
}

export function resizeDesignCanvasNode(surface,id,delta,{snap=0,minSize=12}={}){
  const next=dceClone(surface),node=next.nodes.find(n=>n.id===id);
  if(!node||node.locked)return next;
  let width=node.w+dceFinite(delta.x),height=node.h+dceFinite(delta.y);
  if(snap>0){width=Math.round(width/snap)*snap;height=Math.round(height/snap)*snap;}
  node.w=dceClamp(width,Math.min(node.type==='line'?4:minSize,next.width-node.x),next.width-node.x);
  node.h=dceClamp(height,Math.min(node.type==='line'?2:minSize,next.height-node.y),next.height-node.y);
  return next;
}

export function alignDesignCanvasNodes(surface,ids,edge){
  const next=dceClone(surface),wanted=new Set(ids),nodes=next.nodes.filter(n=>wanted.has(n.id)&&!n.locked);
  if(!nodes.length)return next;
  const left=nodes.length===1?0:Math.min(...nodes.map(n=>n.x)),top=nodes.length===1?0:Math.min(...nodes.map(n=>n.y));
  const right=nodes.length===1?next.width:Math.max(...nodes.map(n=>n.x+n.w)),bottom=nodes.length===1?next.height:Math.max(...nodes.map(n=>n.y+n.h));
  for(const node of nodes){
    if(edge==='left')node.x=left;if(edge==='right')node.x=right-node.w;if(edge==='center')node.x=(left+right-node.w)/2;
    if(edge==='top')node.y=top;if(edge==='bottom')node.y=bottom-node.h;if(edge==='middle')node.y=(top+bottom-node.h)/2;
  }
  return next;
}

export function designCanvasHistory(initial,limit=40){
  let current=dceClone(initial),past=[],future=[];
  return {
    value:()=>dceClone(current),canUndo:()=>past.length>0,canRedo:()=>future.length>0,
    push(next){if(JSON.stringify(next)===JSON.stringify(current))return false;past.push(current);if(past.length>limit)past.shift();current=dceClone(next);future=[];return true;},
    undo(){if(!past.length)return null;future.push(current);current=past.pop();return dceClone(current);},
    redo(){if(!future.length)return null;past.push(current);current=future.pop();return dceClone(current);},
    reset(next){current=dceClone(next);past=[];future=[];},
  };
}

export function setupDesignCanvasEditor({mount,getDesign,onChange,setDesign,onSurfaceChange,getCard=()=>({}),getAssets=()=>({}),renderPage=renderPublicationPage,toast=()=>{}}={}){
  if(!mount||typeof getDesign!=='function')return {refresh:()=>{},setSurface:()=>{},destroy:()=>{},selectedNode:()=>null};
  const change=onChange||setDesign||(()=>{}),surfaceNames={pdfOverview:'PDF · Объект и агент',pdfGallery:'PDF · Фотографии',pdfLocation:'PDF · Расположение',intro:'Intro · Обложка',outro:'Outro · Карта и маршруты'};
  const typeNames={text:'Текст',image:'Фотография',rect:'Плашка',line:'Линия',logo:'Логотип',avatar:'Аватар',map:'Карта',route:'Маршрут',facts:'Характеристики'};
  const bindingRows=Array.isArray(PUBLICATION_BINDINGS)?PUBLICATION_BINDINGS:[];
  const bindingName=id=>bindingRows.find(row=>row.id===id)?.label||id;
  const make=(tag,text='',attrs={})=>{const node=document.createElement(tag);if(text)node.textContent=text;for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));return node;};
  const button=(text,id,action)=>{const node=make('button',text,{type:'button',class:'btn',id});node.onclick=action;return node;};
  const select=(id,choices)=>{const node=make('select','',{id});for(const [value,text] of choices)node.append(make('option',text,{value}));return node;};
  const field=(title,input)=>{const wrap=make('label',title);wrap.htmlFor=input.id;wrap.append(input);return wrap;};
  const normal=()=>normalizePublicationDesign(getDesign());
  let draft=normal(),surfaceId=Object.keys(draft.surfaces||{})[0]||'intro',selection=new Set(),history=designCanvasHistory(draft);
  let scale=1,zoom='fit',showGrid=true,snap=true,gesture=null,renderEpoch=0,renderFrame=null,destroyed=false,resizeObserver=null;
  const host=make('section','',{class:'design-canvas-editor',id:'designCanvasEditor','aria-label':'Редактор расположения блоков'});
  const title=make('div','',{class:'design-canvas-heading'});title.append(make('h3','Редактор макета'),make('p','Перемещайте блоки и меняйте их размеры. Текст из полей карточки подставляется заново для каждого объявления.',{class:'muted'}));
  const toolbar=make('div','',{class:'design-canvas-toolbar'}),surfaces=select('designCanvasSurface',[]),zooms=select('designCanvasZoom',[['fit','По ширине'],['25','25%'],['50','50%'],['75','75%'],['100','100%']]);
  const backgroundInput=make('input','',{type:'color',id:'designCanvasBackground'});
  toolbar.append(field('Полотно',surfaces),field('Масштаб',zooms),field('Фон полотна',backgroundInput));
  const undo=button('↶ Отменить','designCanvasUndo',()=>restore('undo')),redo=button('↷ Повторить','designCanvasRedo',()=>restore('redo'));
  const grid=make('input','',{type:'checkbox',id:'designCanvasGrid'});grid.checked=showGrid;
  const snapping=make('input','',{type:'checkbox',id:'designCanvasSnap'});snapping.checked=snap;
  toolbar.append(undo,redo,field('Сетка',grid),field('Привязка',snapping));
  const workbench=make('div','',{class:'design-canvas-workbench'}),left=make('div','',{class:'design-canvas-workarea'}),viewport=make('div','',{class:'design-canvas-viewport',id:'designCanvasViewport'});
  const page=make('div','',{class:'design-canvas-page',id:'designCanvasPage'}),canvas=make('canvas','',{id:'designCanvasPreview','aria-label':'Точное изображение макета'}),overlay=make('div','',{class:'design-canvas-overlay',id:'designCanvasOverlay',role:'listbox','aria-label':'Блоки макета','aria-multiselectable':'true'});
  page.append(canvas,overlay);viewport.append(page);left.append(viewport);
  const readout=make('p','',{id:'designCanvasReadout',class:'muted'}),warnings=make('p','',{id:'designCanvasWarnings',class:'design-canvas-warnings',role:'status'});left.append(readout,warnings);
  const side=make('div','',{class:'design-canvas-inspector',id:'designCanvasInspector',role:'complementary','aria-label':'Свойства выбранного блока'}),selectedName=make('h4','Выберите блок',{id:'designCanvasSelected'});
  const commands=make('div','',{class:'design-canvas-actions'});
  const addText=button('+ Текст','designCanvasAddText',()=>addNode('text')),addRect=button('+ Плашка','designCanvasAddRect',()=>addNode('rect')),addPhoto=button('+ Фото','designCanvasAddPhoto',()=>addNode('image'));
  const duplicate=button('Копировать блок','designCanvasDuplicate',duplicateNodes),remove=button('Удалить','designCanvasDelete',removeNodes);
  commands.append(addText,addPhoto,addRect,duplicate,remove);
  const inspector=make('fieldset','',{class:'design-canvas-fields'}),coordinates=make('div','',{class:'design-canvas-numbers'}),controls={};
  for(const [key,label] of [['x','X'],['y','Y'],['w','Ширина'],['h','Высота']]){const input=make('input','',{type:'number',id:'designCanvas-'+key,step:'1',min:'0',inputmode:'decimal'});controls[key]=input;coordinates.append(field(label,input));input.onchange=()=>editNode(node=>{node[key]=dceFinite(input.value,node[key]);clampNode(node);},'geometry');}
  inspector.append(coordinates);
  const binding=select('designCanvasBinding',[['','Свой текст'],...bindingRows.map(row=>[row.id,row.label])]),text=make('textarea','',{id:'designCanvasText',rows:4,maxlength:4000});
  binding.onchange=()=>editNode(node=>{node.bind=binding.value;},'binding');
  text.onchange=()=>{if(!selected()||selected().text===text.value)return;editNode(node=>{node.text=text.value;if(node.type==='text')node.bind='';},'text');};
  inspector.append(field('Содержимое из карточки',binding),field('Свой текст',text));
  const fontSize=make('input','',{type:'number',id:'designCanvasFontSize',min:10,max:200,step:1,inputmode:'numeric'}),font=select('designCanvasFont',[['sans','Без засечек'],['serif','С засечками']]),weight=select('designCanvasWeight',[['400','Обычный'],['500','Средний'],['600','Полужирный'],['700','Жирный'],['800','Очень жирный']]),align=select('designCanvasAlign',[['left','Слева'],['center','По центру'],['right','Справа']]);
  const textColor=make('input','',{type:'color',id:'designCanvasColor'}),fill=make('input','',{type:'color',id:'designCanvasFill'}),opacity=make('input','',{type:'range',id:'designCanvasOpacity',min:0,max:1,step:0.05});
  const visible=make('input','',{type:'checkbox',id:'designCanvasVisible'}),locked=make('input','',{type:'checkbox',id:'designCanvasLocked'}),objectFit=select('designCanvasFit',[['cover','Заполнить с кадрированием'],['contain','Показать целиком']]),photoIndex=make('input','',{type:'number',id:'designCanvasPhotoIndex',min:1,max:30,step:1});
  const styleGrid=make('div','',{class:'design-canvas-numbers'});styleGrid.append(field('Размер шрифта',fontSize),field('Шрифт',font),field('Начертание',weight),field('Текст',align),field('Цвет текста / линии',textColor),field('Заливка',fill));inspector.append(styleGrid,field('Непрозрачность',opacity),field('Показывать блок',visible),field('Зафиксировать блок',locked),field('Изображение',objectFit),field('Номер фотографии',photoIndex));
  fontSize.onchange=()=>editStyle('fontSize',dceClamp(dceFinite(fontSize.value,36),10,200));font.onchange=()=>editStyle('fontFamily',font.value);weight.onchange=()=>editStyle('fontWeight',Number(weight.value));align.onchange=()=>editStyle('align',align.value);textColor.onchange=()=>editStyle('color',textColor.value);fill.onchange=()=>editStyle('background',fill.value);opacity.onchange=()=>editStyle('opacity',Number(opacity.value));visible.onchange=()=>editNode(n=>{n.visible=visible.checked;},'visibility');locked.onchange=()=>editNode(n=>{n.locked=locked.checked;},'lock');objectFit.onchange=()=>editStyle('objectFit',objectFit.value);photoIndex.onchange=()=>editNode(n=>{n.photoIndex=dceClamp(Math.round(Number(photoIndex.value)||1)-1,0,29);n.bind='photo'+(n.photoIndex+1);},'photo');
  const alignRow=make('div','',{class:'design-canvas-actions'});
  for(const [key,label] of [['left','←'],['center','↔'],['right','→'],['top','↑'],['middle','↕'],['bottom','↓']]){const b=button(label,'designCanvasAlign-'+key,()=>{replaceSurface(alignDesignCanvasNodes(surface(),[...selection],key));commit('align');});b.title='Выровнять: '+({left:'слева',center:'по горизонтальному центру',right:'справа',top:'сверху',middle:'по вертикальному центру',bottom:'снизу'}[key]);b.setAttribute('aria-label',b.title);alignRow.append(b);}
  const layerActions=make('div','',{class:'design-canvas-actions'});layerActions.append(button('На слой выше','designCanvasForward',()=>changeLayer(1)),button('На слой ниже','designCanvasBackward',()=>changeLayer(-1)));
  const layers=make('div','',{class:'design-canvas-layers',id:'designCanvasLayers','aria-label':'Слои'});
  side.append(selectedName,commands,inspector,alignRow,layerActions,make('h4','Слои'),layers,make('p','Стрелки — сдвиг на 1; Shift + стрелки — на 10. Ctrl/⌘ + Z — отмена. Shift + нажатие выбирает несколько блоков.',{class:'muted'}));
  workbench.append(left,side);host.append(title,toolbar,workbench);mount.replaceChildren(host);

  function surface(){return draft.surfaces[surfaceId];}
  function selected(){return surface()?.nodes.find(node=>selection.has(node.id))||null;}
  function nodeName(node){return node.label||node.name||(node.bind?bindingName(node.bind):typeNames[node.type]||'Блок');}
  function clampNode(node){const s=surface();node.w=dceClamp(dceFinite(node.w,100),node.type==='line'?4:12,s.width);node.h=dceClamp(dceFinite(node.h,60),node.type==='line'?2:12,s.height);node.x=dceClamp(dceFinite(node.x),0,s.width-node.w);node.y=dceClamp(dceFinite(node.y),0,s.height-node.h);}
  function replaceSurface(next){draft.surfaces[surfaceId]=next;}
  function notify(reason){try{const result=change(dceClone(draft),{reason,surface:surfaceId});if(result?.catch)result.catch(error=>toast(error.message||'Не удалось обновить макет'));}catch(error){toast(error.message||'Не удалось обновить макет');}}
  function commit(reason){draft=normalizePublicationDesign(draft);if(history.push(draft))notify(reason);refreshUI();}
  function editNode(edit,reason='edit'){const node=selected();if(!node)return;edit(node);commit(reason);}
  function editStyle(key,value){editNode(node=>{node.style={...(node.style||{}),[key]:value};},'style');}
  function restore(direction){const value=history[direction]();if(!value)return;draft=value;selection=new Set([...selection].filter(id=>surface()?.nodes.some(n=>n.id===id)));notify(direction);refreshUI();}
  function selectNode(node,event={},preserveElements=false){if(event.shiftKey){if(selection.has(node.id))selection.delete(node.id);else selection.add(node.id);}else if(!selection.has(node.id)||selection.size!==1)selection=new Set([node.id]);dimensions();refreshOverlay(!preserveElements);refreshInspector();}
  function startGesture(event,node,kind){if(event.button!==undefined&&event.button!==0)return;if(!selection.has(node.id))selectNode(node,event,true);else if(event.shiftKey&&kind!=='resize'){selectNode(node,event,true);return;}event.currentTarget.closest?.('.design-canvas-hit')?.focus({preventScroll:true});if(node.locked)return;event.preventDefault();event.stopPropagation();const target=overlay;try{target.setPointerCapture?.(event.pointerId);}catch{}gesture={pointerId:event.pointerId,target,start:{x:event.clientX,y:event.clientY},bounds:page.getBoundingClientRect(),before:dceClone(surface()),kind,id:node.id,ids:[...selection]};}
  function moveGesture(event){if(!gesture||event.pointerId!==gesture.pointerId)return;event.preventDefault();const delta=designCanvasDelta(gesture.start,{x:event.clientX,y:event.clientY},gesture.bounds,gesture.before);replaceSurface(gesture.kind==='resize'?resizeDesignCanvasNode(gesture.before,gesture.id,delta,{snap:snap?10:0}):moveDesignCanvasNodes(gesture.before,gesture.ids,delta,{snap:snap?10:0}));refreshOverlay(false);refreshInspector();queueRender();}
  function finishGesture(event,cancelled=false){if(!gesture||event.pointerId!==gesture.pointerId)return;const previous=gesture;gesture=null;try{previous.target.releasePointerCapture?.(event.pointerId);}catch{}if(cancelled){replaceSurface(previous.before);refreshUI();return;}commit(previous.kind);}
  overlay.addEventListener('pointermove',moveGesture);overlay.addEventListener('pointerup',event=>finishGesture(event));overlay.addEventListener('pointercancel',event=>finishGesture(event,true));
  overlay.addEventListener('pointerdown',event=>{if(event.target===overlay){selection.clear();refreshOverlay();refreshInspector();}});
  function refreshOverlay(rebuild=true){
    const s=surface();if(!s)return;if(rebuild){const ids=new Set(s.nodes.map(node=>node.id));for(const child of [...overlay.children])if(!ids.has(child.dataset.nodeId))child.remove();}
    for(const node of [...s.nodes].sort((a,b)=>(a.z||0)-(b.z||0))){
      let box=[...overlay.children].find(el=>el.dataset.nodeId===node.id);
      if(!box){box=make('div','',{class:'design-canvas-hit',role:'option',tabindex:0,'aria-label':nodeName(node)});box.dataset.nodeId=node.id;overlay.append(box);}box.onpointerdown=event=>startGesture(event,node,'move');box.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectNode(node,event);}};box.setAttribute('aria-label',nodeName(node));
      box.classList.toggle('is-selected',selection.has(node.id));box.classList.toggle('is-hidden',node.visible===false);box.classList.toggle('is-locked',!!node.locked);box.setAttribute('aria-selected',String(selection.has(node.id)));box.style.left=node.x/s.width*100+'%';box.style.top=node.y/s.height*100+'%';box.style.width=node.w/s.width*100+'%';box.style.height=node.h/s.height*100+'%';box.style.zIndex=String((node.z||0)+1);box.style.pointerEvents=node.visible===false?'none':'auto';
      let handle=box.querySelector('.design-canvas-resize');if(selection.has(node.id)&&!node.locked){if(!handle){handle=make('span','',{class:'design-canvas-resize','aria-label':'Изменить размер '+nodeName(node)});handle.onpointerdown=event=>startGesture(event,node,'resize');box.append(handle);}}else handle?.remove();
    }
  }
  function refreshInspector(){
    const node=selected();inspector.disabled=!node;selectedName.textContent=node?nodeName(node)+(selection.size>1?' · выбрано '+selection.size:''):'Выберите блок';duplicate.disabled=remove.disabled=!node;
    for(const input of Object.values(controls))input.value=node?String(Math.round(node[input.id.split('-').at(-1)]*10)/10):'';
    if(!node){layers.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed','false'));return;}
    const style=node.style||{};if(node.bind&&![...binding.options].some(option=>option.value===node.bind))binding.append(make('option',bindingName(node.bind),{value:node.bind}));binding.value=node.bind||'';text.value=node.text||'';text.disabled=node.type!=='text';binding.disabled=!['text','facts','route','image'].includes(node.type);
    fontSize.value=String(style.fontSize||36);font.value=style.fontFamily||'sans';weight.value=String(style.fontWeight||400);align.value=style.align||'left';textColor.value=/^#[a-f\d]{6}$/i.test(style.color||'')?style.color:'#ffffff';fill.value=/^#[a-f\d]{6}$/i.test(style.background||'')?style.background:'#07162b';opacity.value=String(style.opacity??1);visible.checked=node.visible!==false;locked.checked=!!node.locked;objectFit.value=style.objectFit||'cover';photoIndex.value=String((node.photoIndex||0)+1);photoIndex.disabled=node.type!=='image';objectFit.disabled=!['image','logo','avatar','map'].includes(node.type);
    layers.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(selection.has(b.dataset.nodeId))));
  }
  function refreshLayers(){const focused=layers.contains(document.activeElement)?document.activeElement?.dataset.nodeId:null;layers.replaceChildren();for(const node of [...surface().nodes].sort((a,b)=>(b.z||0)-(a.z||0))){const b=button((node.visible===false?'◌ ':node.locked?'🔒 ':'')+nodeName(node),'',event=>selectNode(node,event));b.removeAttribute('id');b.dataset.nodeId=node.id;b.setAttribute('aria-pressed',String(selection.has(node.id)));layers.append(b);}if(focused)[...layers.children].find(node=>node.dataset.nodeId===focused)?.focus({preventScroll:true});}
  function dimensions(){const s=surface();if(!s)return;const fit=Math.min(1,Math.max(.08,(viewport.clientWidth-28)/s.width));scale=zoom==='fit'?fit:Number(zoom)/100;page.style.width=s.width*scale+'px';page.style.height=s.height*scale+'px';page.classList.toggle('has-grid',showGrid);page.style.setProperty('--dce-grid',String(40*scale)+'px');readout.textContent=`${s.width} × ${s.height} · ${Math.round(scale*100)}% · выделено ${selection.size}. Экспорт сохраняет полный размер.`;}
  async function draw(){const epoch=++renderEpoch,s=surface();if(!s||destroyed)return;const design=dceClone(draft),card=dceClone(getCard()||{});const buffer=document.createElement('canvas');buffer.width=s.width;buffer.height=s.height;try{const assets=await getAssets();const result=await renderPage(buffer,surfaceId,design,card,assets||{});if(destroyed||epoch!==renderEpoch)return;canvas.width=s.width;canvas.height=s.height;canvas.getContext('2d').drawImage(buffer,0,0);warnings.textContent=(result?.warnings||[]).slice(0,3).map(value=>typeof value==='string'?value:value.message||'Проверьте расположение блока').join(' · ');}catch(error){if(epoch===renderEpoch&&!destroyed)warnings.textContent=error.message||'Не удалось построить полотно';}}
  function queueRender(){if(renderFrame!==null)return;renderFrame=requestAnimationFrame(()=>{renderFrame=null;draw();});}
  function refreshUI(){if(destroyed)return;dimensions();backgroundInput.value=surface().background||'#07162b';refreshOverlay();refreshLayers();refreshInspector();undo.disabled=!history.canUndo();redo.disabled=!history.canRedo();queueRender();}
  function addNode(type){const s=surface();if(s.nodes.length>=80)return toast('На одном полотне можно разместить до 80 блоков.');const ids=new Set(s.nodes.map(node=>node.id));let count=1,id;do{id='custom-'+type+'-'+count++;}while(ids.has(id));s.nodes.push({id,type,x:Math.min(60,s.width-120),y:Math.min(60,s.height-100),w:Math.min(type==='text'?500:420,s.width-60),h:type==='text'?100:220,text:type==='text'?'自定义文字':'',bind:type==='image'?'photo1':'',photoIndex:0,visible:true,z:Math.max(0,...s.nodes.map(n=>n.z||0))+1,style:{fontSize:42,color:'#ffffff',fontFamily:'sans',fontWeight:600,align:'left',opacity:1,background:type==='rect'?'#12345b':''}});selection=new Set([id]);commit('add');}
  function duplicateNodes(){const s=surface(),copies=s.nodes.filter(node=>selection.has(node.id));if(s.nodes.length+copies.length>80)return toast('Недостаточно места: на одном полотне допускается до 80 блоков.');const ids=new Set(s.nodes.map(node=>node.id)),added=[];let z=Math.max(0,...s.nodes.map(n=>n.z||0));for(const original of copies){let i=1,id;do{id=original.id+'-copy-'+i++;}while(ids.has(id));ids.add(id);const node={...dceClone(original),id,z:++z,locked:false,x:Math.min(s.width-original.w,original.x+24),y:Math.min(s.height-original.h,original.y+24)};s.nodes.push(node);added.push(id);}selection=new Set(added);commit('duplicate');}
  function removeNodes(){replaceSurface({...surface(),nodes:surface().nodes.filter(node=>!selection.has(node.id)||node.locked)});selection.clear();commit('delete');}
  function changeLayer(step){const s=surface(),ordered=[...s.nodes].sort((a,b)=>(a.z||0)-(b.z||0));for(const node of (step>0?[...ordered].reverse():ordered)){if(!selection.has(node.id))continue;const index=ordered.indexOf(node),target=index+step;if(target<0||target>=ordered.length||selection.has(ordered[target].id))continue;[ordered[index],ordered[target]]=[ordered[target],ordered[index]];}ordered.forEach((node,index)=>{node.z=index;});commit('layer');}
  function handleKeyboard(event){if(destroyed||!host.contains(document.activeElement)||/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)||event.target.isContentEditable)return;const mod=event.metaKey||event.ctrlKey;if(mod&&event.key.toLowerCase()==='z'){event.preventDefault();restore(event.shiftKey?'redo':'undo');return;}if(mod&&event.key.toLowerCase()==='y'){event.preventDefault();restore('redo');return;}if(mod&&event.key.toLowerCase()==='d'){event.preventDefault();duplicateNodes();return;}if(event.key==='Delete'||event.key==='Backspace'){event.preventDefault();removeNodes();return;}const directions={ArrowLeft:{x:-1,y:0},ArrowRight:{x:1,y:0},ArrowUp:{x:0,y:-1},ArrowDown:{x:0,y:1}};if(directions[event.key]&&selection.size){event.preventDefault();const direction=directions[event.key],distance=event.shiftKey?10:1;replaceSurface(moveDesignCanvasNodes(surface(),[...selection],{x:direction.x*distance,y:direction.y*distance}));commit('keyboard');}}
  host.addEventListener('keydown',handleKeyboard);
  surfaces.onchange=()=>{const id=surfaces.value;if(setSurface(id))onSurfaceChange?.(id);};zooms.onchange=()=>{zoom=zooms.value;dimensions();};grid.onchange=()=>{showGrid=grid.checked;dimensions();};snapping.onchange=()=>{snap=snapping.checked;};backgroundInput.onchange=()=>{surface().background=backgroundInput.value;commit('background');};
  function surfaceOptions(){surfaces.replaceChildren();for(const [id,s] of Object.entries(draft.surfaces||{}))surfaces.append(make('option',surfaceNames[id]||s.label||id,{value:id}));}
  function clearPreview(){canvas.width=surface().width;canvas.height=surface().height;const context=canvas.getContext('2d');context.fillStyle=surface().background||'#07162b';context.fillRect(0,0,canvas.width,canvas.height);warnings.textContent='Обновляем предпросмотр…';}
  function setSurface(id){if(destroyed||!draft.surfaces[id])return false;if(gesture)finishGesture({pointerId:gesture.pointerId},true);surfaceId=id;surfaces.value=id;selection.clear();++renderEpoch;clearPreview();refreshUI();return true;}
  function refresh({resetHistory=false}={}){if(destroyed)return;gesture=null;++renderEpoch;draft=normal();if(!draft.surfaces[surfaceId])surfaceId=Object.keys(draft.surfaces)[0];selection=new Set([...selection].filter(id=>surface()?.nodes.some(node=>node.id===id)));if(resetHistory)history.reset(draft);else if(JSON.stringify(draft)!==JSON.stringify(history.value()))history.push(draft);surfaceOptions();surfaces.value=surfaceId;clearPreview();refreshUI();}
  surfaceOptions();surfaces.value=surfaceId;
  if(typeof ResizeObserver==='function'){resizeObserver=new ResizeObserver(()=>dimensions());resizeObserver.observe(viewport);}else window.addEventListener('resize',dimensions);
  refreshUI();
  return {refresh,setSurface,selectedNode:()=>selected()?dceClone(selected()):null,design:()=>dceClone(draft),destroy(){destroyed=true;++renderEpoch;if(renderFrame!==null)cancelAnimationFrame(renderFrame);resizeObserver?.disconnect();window.removeEventListener('resize',dimensions);mount.replaceChildren();}};
}
