// Export source authority is received with a loaded/saved card. A later file
// list cannot renew it: that list says nothing about the content on screen.
const loadedPresentationSources=new WeakMap();
const presentationSourceChanged=()=>Object.assign(new Error('Карточка изменилась. Загрузите её заново, обновите макет и повторите экспорт.'),{code:'CLIENT_REVISION_CHANGED'});

export function presentationSourceBoundary(resultId,receipt){
 if(!/^[a-f0-9]{32}$/.test(resultId||'')||receipt?.version!==1||receipt.cardId!==resultId||!/^[a-f0-9]{64}$/.test(receipt.revisionSha256||'')||!/^[a-f0-9]{64}$/.test(receipt.signature||''))throw new Error('Обновите страницу и загрузите карточку заново: подтверждение исходного содержимого недоступно.');
 return {resultId,expectedRevision:receipt.revisionSha256,sourceReceipt:{version:1,cardId:resultId,revisionSha256:receipt.revisionSha256,signature:receipt.signature}};
}

export function captureLoadedPresentationSource(card){
 if(card&&typeof card==='object'){
  // Older servers and viewer projections can still be read. Export requires
  // a receipt and an unchanged authenticated card object, checked below.
  loadedPresentationSources.set(card,{serialized:JSON.stringify(card),receipt:card.presentationReceipt?JSON.parse(JSON.stringify(card.presentationReceipt)):null});
 }
 return card;
}

export function loadedPresentationSource(card){
 const loaded=card&&loadedPresentationSources.get(card);
 if(!loaded||loaded.serialized!==JSON.stringify(card))throw presentationSourceChanged();
 return presentationSourceBoundary(card.id,loaded.receipt);
}

export async function savePresentationSource({getCard,saveCard,cardKey}){
 const key=cardKey(getCard()),saved=await saveCard();
 if(cardKey(getCard())!==key)throw presentationSourceChanged();
 return presentationSourceBoundary(saved.result_id,saved.presentationReceipt);
}

export async function beginPresentationExport({getCard,ensureCard,cardKey}){
 const key=cardKey(getCard()),source=await ensureCard({exportSnapshot:true});
 if(cardKey(getCard())!==key)throw presentationSourceChanged();
 const boundary=presentationSourceBoundary(source?.resultId,source?.sourceReceipt);
 if(source.expectedRevision!==boundary.expectedRevision)throw presentationSourceChanged();
 return {...boundary,key};
}

export function assertPresentationExportCurrent(snapshot,boundary,{getCard,cardKey}){
 if(!boundary||snapshot?.key!==boundary.key||cardKey(getCard())!==boundary.key)throw presentationSourceChanged();
 return boundary.resultId;
}
