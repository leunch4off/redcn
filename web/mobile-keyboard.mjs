// Respond to the visible viewport rather than guessing a physical keyboard
// from a user agent. Desktop keyboard/viewport tests and physical iOS checks
// are recorded separately.
export function mobileKeyboardAdjustment(rect,viewport,{margin=12,obstruction=0}={}){
 const top=viewport.top+margin,bottom=viewport.top+viewport.height-margin-obstruction;
 if(!Number.isFinite(top)||!Number.isFinite(bottom)||bottom<=top)return 0;
 if(rect.top<top)return rect.top-top;
 if(rect.height>bottom-top)return rect.top-top;
 return rect.bottom>bottom?rect.bottom-bottom:0;
}

export function setupMobileKeyboard({document:doc=globalThis.document,window:win=globalThis.window}={}){
 if(!doc?.documentElement||!win)return {destroy(){},snapshot:()=>({reducedViewportObserved:false})};
 const root=doc.documentElement,viewport=win.visualViewport;
 let frame=null,reducedViewportObserved=false,maximumHeight=win.innerHeight;
 const visible=()=>({top:Number(viewport?.offsetTop)||0,height:Number(viewport?.height)||win.innerHeight,width:Number(viewport?.width)||win.innerWidth});
 function update(){
  frame=null;const view=visible();maximumHeight=Math.max(maximumHeight,win.innerHeight,view.height);
  const narrowed=view.width<=600,keyboard=narrowed&&maximumHeight-view.height>120;
  reducedViewportObserved=reducedViewportObserved||keyboard;
  root.dataset.mobileKeyboard=keyboard?'open':'closed';root.style.setProperty('--redcn-visible-height',view.height+'px');
  const active=doc.activeElement;
  if(!narrowed||!active?.matches?.('input:not([type=checkbox]):not([type=radio]),textarea,select,[contenteditable=true]')||active.disabled||active.readOnly)return;
  const rect=active.getBoundingClientRect(),footer=doc.getElementById('phoneTabs');let obstruction=0;
  if(footer&&!footer.classList.contains('hidden')){const f=footer.getBoundingClientRect();if(f.top<view.top+view.height&&f.bottom>view.top)obstruction=Math.max(0,view.top+view.height-Math.max(view.top,f.top));}
  const delta=mobileKeyboardAdjustment(rect,view,{obstruction});
  if(Math.abs(delta)>1)win.scrollBy({top:delta,left:0,behavior:'instant'});
 }
 function schedule(){if(frame===null)frame=win.requestAnimationFrame(update);}
 doc.addEventListener('focusin',schedule);win.addEventListener('resize',schedule);
 viewport?.addEventListener('resize',schedule);viewport?.addEventListener('scroll',schedule);schedule();
 return {snapshot:()=>({reducedViewportObserved,viewportWidth:Math.round(visible().width),viewportHeight:Math.round(win.innerHeight),visualViewportHeight:Math.round(visible().height)}),
  destroy(){doc.removeEventListener('focusin',schedule);win.removeEventListener('resize',schedule);viewport?.removeEventListener('resize',schedule);viewport?.removeEventListener('scroll',schedule);if(frame!==null)win.cancelAnimationFrame(frame);delete root.dataset.mobileKeyboard;root.style.removeProperty('--redcn-visible-height');}};
}
