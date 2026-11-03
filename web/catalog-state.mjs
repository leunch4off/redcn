// Presentation order is independent from the server's change-feed timestamp.
function catalogTimestamp(value){
 if(typeof value==='number')return Number.isFinite(value)&&value>=0?value:null;
 if(typeof value!=='string'||!value.trim())return null;
 const parsed=Date.parse(value);return Number.isFinite(parsed)&&parsed>=0?parsed/1000:null;
}
export function catalogOrderValue(card){return catalogTimestamp(card?.catalogOrder)??catalogTimestamp(card?.addedAt)??catalogTimestamp(card?.updated)??0;}
export function compareCatalogCards(a,b){return catalogOrderValue(b)-catalogOrderValue(a)||String(a?.id||'').localeCompare(String(b?.id||''));}
export function catalogWasViewed(card){return card?.viewed===true||catalogTimestamp(card?.viewedAt)!==null;}
export function catalogMatchesFilter(card,mode='all'){return mode!=='edited'||card?.contentEdited===true;}
function catalogViewedState(previous,current){
  const viewed=catalogWasViewed(previous)||catalogWasViewed(current);
  const candidates=[previous?.viewedAt,current?.viewedAt].filter(value=>catalogTimestamp(value)!==null);
  candidates.sort((a,b)=>catalogTimestamp(a)-catalogTimestamp(b));
  return {viewed,viewedAt:viewed?(candidates[0]??null):null};
}
// A view acknowledgement can arrive after an edit. It owns only these two
// fields, never publication metadata, ordering or the synchronization stamp.
export function mergeCatalogViewed(previous,acknowledgement){
  return {...previous,...catalogViewedState(previous,acknowledgement)};
}
export function mergeCatalogCard(previous,current){
 const merged={...previous,...current};
 if(catalogTimestamp(current?.catalogOrder)===null)merged.catalogOrder=catalogOrderValue(previous||current);
  // There is no unview action. Within one authenticated reader scope a late
  // feed snapshot cannot undo a confirmed view; callers clear on scope change.
  return {...merged,...catalogViewedState(previous,current)};
}
function catalogPublication(card){
 const clean=value=>typeof value==='string'?value.replace(/\r\n?/g,'\n').trim():'';
  return JSON.stringify([clean(card?.title??card?.titles?.[0]),clean(card?.output??card?.body),clean(card?.tags)]);
}
export function saveBrowserCatalogCard(objects,card,at=new Date().toISOString()){
 const index=objects.findIndex(row=>row.id===card.id),previous=index>=0?objects[index]:null;
 const edited=!!previous&&catalogPublication(previous)!==catalogPublication(card);
 const saved={...card,addedAt:previous?(previous.addedAt??null):at,
  contentEdited:previous?.contentEdited===true||edited,
  contentEditedAt:edited?at:previous?.contentEditedAt??null,
  viewed:catalogWasViewed(previous),viewedAt:previous?.viewedAt??null};
 if(index<0)return [saved,...objects];
 return objects.map((row,position)=>position===index?saved:row);
}
export function markBrowserCatalogViewed(objects,id,at=new Date().toISOString()){
 return objects.map(row=>row.id===id?{...row,viewed:true,viewedAt:row.viewedAt??at}:row);
}
