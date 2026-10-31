export function furniturePhotoIndex(top,height,viewport,count){
  if(count<2)return 0;
  const distance=Math.max(1,height-viewport*.35);
  const progress=Math.max(0,Math.min(1,(viewport*.25-top)/distance));
  return Math.min(count-1,Math.floor(progress*count));
}

export function setupFurniturePhotos({getItems,onOpen,read,write}){
  const el=id=>document.getElementById(id),section=el('furnitureSection'),dock=el('furniturePhotoDock');
  const grid=section.querySelector('.feature-grid'),home=dock.parentElement,editor=el('editor');
  const image=el('furniturePhotoImage'),counter=el('furniturePhotoCounter'),thumbs=el('furniturePhotoThumbs');
  const auto=el('furniturePhotoAuto');auto.checked=read('most-furniture-photo-follow',true);
  let active=0,rows=[],signature='',frame=0,previewUrl=null,previewBlob=null,previewSource='',activeUrl='';
  function showPhoto(){
    const item=rows[active];if(!item)return;
    const blob=item.cropBlob||item.blob;
    if(blob!==previewBlob||item.url!==previewSource){
      if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=null;previewBlob=blob;previewSource=item.url;
      if(blob)previewUrl=URL.createObjectURL(blob);
      image.src=previewUrl||item.url;
    }
    activeUrl=item.url;image.alt='Фото квартиры '+(active+1)+' из '+rows.length;
    counter.textContent=(active+1)+' / '+rows.length;
    el('furniturePhotoPrev').disabled=rows.length<2;el('furniturePhotoNext').disabled=rows.length<2;
    [...thumbs.children].forEach((button,i)=>button.setAttribute('aria-pressed',String(i===active)));
    const currentThumb=thumbs.children[active];if(currentThumb)thumbs.scrollLeft=currentThumb.offsetLeft-thumbs.clientWidth/2+currentThumb.offsetWidth/2;
  }
  function choose(index,manual=false){
    if(!rows.length)return;
    active=(index+rows.length)%rows.length;
    if(manual){auto.checked=false;write('most-furniture-photo-follow',false);}
    showPhoto();
  }
  function position(){
    frame=0;const rect=section.getBoundingClientRect(),viewport=window.innerHeight;
    const visible=rows.length>0&&editor.getClientRects().length>0&&rect.top<viewport*.65&&rect.bottom>80;
    const inline=window.innerWidth<=1050;
    if(inline&&dock.parentElement!==section)section.insertBefore(dock,grid);
    if(!inline&&dock.parentElement!==home)home.prepend(dock);
    dock.classList.toggle('is-inline',inline);dock.classList.toggle('hidden',!visible);
    if(!visible)return;
    if(!inline){
      const column=home.closest('#editor > .grid > div')||home.parentElement;
      const bounds=column.getBoundingClientRect();dock.style.left=bounds.left+'px';dock.style.width=bounds.width+'px';
    }else{dock.style.removeProperty('left');dock.style.removeProperty('width');}
    if(auto.checked){const next=furniturePhotoIndex(rect.top,rect.height,viewport,rows.length);if(next!==active)choose(next);}
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(position);}
  function render(){
    rows=getItems().filter(item=>item.selected!==false);
    const nextSignature=rows.map(item=>item.url).join('\n');
    if(nextSignature!==signature){
      signature=nextSignature;active=Math.max(0,rows.findIndex(item=>item.url===activeUrl));thumbs.replaceChildren();
      rows.forEach((item,i)=>{const button=document.createElement('button');button.type='button';button.className='btn';button.textContent=String(i+1);button.setAttribute('aria-label','Показать фото '+(i+1));button.onclick=()=>choose(i,true);thumbs.append(button);});
    }
    if(rows.length){active=Math.min(active,rows.length-1);showPhoto();}
    else{if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=null;previewBlob=null;previewSource='';image.removeAttribute('src');}
    schedule();
  }
  el('furniturePhotoPrev').onclick=()=>choose(active-1,true);el('furniturePhotoNext').onclick=()=>choose(active+1,true);
  el('furniturePhotoOpen').onclick=()=>{if(rows[active])onOpen(rows[active]);};
  auto.onchange=()=>{write('most-furniture-photo-follow',auto.checked);schedule();};
  dock.addEventListener('keydown',event=>{if(event.target!==el('furniturePhotoOpen'))return;if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();choose(active+(event.key==='ArrowRight'?1:-1),true);}});
  window.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule,{passive:true});
  new ResizeObserver(schedule).observe(section);new MutationObserver(schedule).observe(editor,{attributes:true,attributeFilter:['class']});
  render();return {render};
}
