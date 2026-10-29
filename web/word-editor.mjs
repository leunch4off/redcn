import {phraseEntries,tokenize,replaceSpan} from './content-tools.mjs';

export function setupWordEditor({getRules,getCustom,saveCustom,onChange,toast}) {
  const el=id=>document.getElementById(id);let selected=null,undo=[],timer;
  const entries=()=>phraseEntries(getRules(),getCustom());
  const snapshot=()=>({title:el('outputTitle').value,body:el('output').value});
  function render(){
    if(el('wordPanel').classList.contains('hidden'))return;
    const dictionary=entries();
    for(const [field,target] of [['outputTitle','titleTokens'],['output','bodyTokens']]){
      el(target).replaceChildren();const text=el(field).value;
      if(!text)el(target).textContent='Сначала создайте текст.';
      for(const token of tokenize(text,dictionary)){
        if(/^\s+$/.test(token.text)){el(target).append(document.createTextNode(token.text));continue;}
        const button=document.createElement('button');button.className='word '+(token.entry?'known':'unknown');button.type='button';button.textContent=token.text;
        button.title=token.entry?.ru||'Нет перевода — добавить в словарь';button.onclick=()=>choose({...token,field});el(target).append(button);
      }
    }
  }
  function choose(token){selected=token;el('wordSelection').textContent=token.text;el('wordMeaning').textContent=token.entry?.ru||'Перевода этой фразы пока нет в словаре. Добавьте русское значение ниже.';el('wordReplacement').value='';el('wordRussian').value=token.entry?.ru||'';el('wordOptions').replaceChildren();
    for(const alternative of token.entry?.alternatives||[]){const button=document.createElement('button');button.className='btn';button.textContent=alternative;button.onclick=()=>{el('wordReplacement').value=alternative;};el('wordOptions').append(button);}
    el('wordInspector').classList.remove('hidden');el('wordApply').disabled=false;
  }
  el('wordToggle').onclick=()=>{const hidden=el('wordPanel').classList.toggle('hidden');el('wordToggle').setAttribute('aria-expanded',String(!hidden));render();};
  function apply(){if(!selected)return toast('Выберите слово или выделите фразу');const replacement=el('wordReplacement').value.trim();if(!replacement)return toast('Укажите замену');
    try{const previous=snapshot();const field=el(selected.field);replaceSpan(field.value,selected.start,selected.end,selected.text,replacement);
      if(el('wordReplaceAll').checked){for(const name of ['outputTitle','output'])el(name).value=el(name).value.split(selected.text).join(replacement);}
      else field.value=replaceSpan(field.value,selected.start,selected.end,selected.text,replacement);
      undo.push(previous);undo=undo.slice(-20);el('wordUndo').disabled=false;
      if(el('wordRemember').checked&&el('wordRussian').value.trim()){
        const custom=getCustom().filter(e=>e.zh!==replacement);custom.push({zh:replacement,ru:el('wordRussian').value.trim(),alternatives:[selected.text]});saveCustom(custom);
      }
      selected=null;el('wordInspector').classList.add('hidden');onChange();render();toast('Замена применена. Проверьте смысл фразы.');
    }catch(error){toast(error.message);}
  }
  el('wordApply').onclick=apply;
  el('wordUndo').onclick=()=>{const previous=undo.pop();if(!previous)return;el('outputTitle').value=previous.title;el('output').value=previous.body;el('wordUndo').disabled=!undo.length;selected=null;el('wordInspector').classList.add('hidden');onChange();render();toast('Замена отменена');};
  el('wordSearch').oninput=()=>{el('wordSearchResults').replaceChildren();const query=el('wordSearch').value.trim().toLowerCase();if(!query)return;
    for(const entry of entries().filter(e=>(e.zh+' '+e.ru).toLowerCase().includes(query)).slice(0,20)){
      const button=document.createElement('button');button.className='btn word-result';button.textContent=entry.zh+' — '+entry.ru;button.onclick=()=>{if(!selected)return toast('Сначала выберите слово в тексте');el('wordReplacement').value=entry.zh;el('wordRussian').value=entry.ru;};el('wordSearchResults').append(button);
    }
  };
  for(const field of ['outputTitle','output']){
    el(field).addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(render,200);});
    const inspectSelection=()=>{const start=el(field).selectionStart,end=el(field).selectionEnd;if(end<=start)return;const text=el(field).value.slice(start,end);choose({field,start,end,text,entry:entries().find(e=>e.zh===text)});el('wordPanel').classList.remove('hidden');el('wordToggle').setAttribute('aria-expanded','true');render();};
    for(const event of ['mouseup','keyup','select'])el(field).addEventListener(event,inspectSelection);
  }
  function reset(){selected=null;undo=[];el('wordUndo').disabled=true;el('wordInspector').classList.add('hidden');}
  return {render,reset};
}
