// Inert, bounded HTML utilities. Never evaluate page scripts or hydration state.
export function listingDecode(value){return String(value||'').replace(/&(#x[\da-f]+|#\d+|nbsp|amp|quot|apos|lt|gt);/gi,(_,key)=>{if(key[0]==='#'){const n=parseInt(key.slice(key[1].toLowerCase()==='x'?2:1),key[1].toLowerCase()==='x'?16:10);return n>0&&n<=0x10ffff?String.fromCodePoint(n):'';}return {nbsp:' ',amp:'&',quot:'"',apos:"'",lt:'<',gt:'>'}[key.toLowerCase()];});}
export const listingClean=value=>listingDecode(value).replace(/[ \t\r\f\u00a0]+/g,' ').replace(/ *\n */g,'\n').replace(/\n{2,}/g,'\n').trim();
export const listingExcluded=node=>/similar|recommend|seller-info|item-view-contacts|messenger/i.test((node.attrs['data-marker']||'')+' '+(node.attrs['data-name']||''))||['nav','header','footer','form','ymaps'].includes(node.tag);
export function listingDescendants(node){const out=[],todo=[...node.children].reverse();while(todo.length){const n=todo.pop();if(typeof n==='string'||listingExcluded(n))continue;out.push(n);todo.push(...n.children.slice().reverse());}return out;}
export function listingText(node){const parts=[],todo=[node];let length=0;while(todo.length&&length<50000){const n=todo.pop();if(typeof n==='string'){parts.push(n);length+=n.length;continue;}if(listingExcluded(n)||n.tag==='button')continue;if(/^(div|p|li|h[1-6]|dt|dd|br)$/.test(n.tag))parts.push('\n');todo.push(...n.children.slice().reverse());}return listingClean(parts.join(''));}
export function listingDocument(input){
 if(typeof input!=='string'||input.length>8_000_000)throw Error('HTML должен быть текстом размером до 8 МБ');
 const html=input.replace(/<!--[\s\S]*?-->/g,'').replace(/<(script|style|noscript|template)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,'');
 const root={tag:'root',attrs:{},children:[]},stack=[root],nodes=[];
 const empty=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
 for(const token of html.matchAll(/<(?:[^"'<>]|"[^"]*"|'[^']*')*>|[^<]+/g)){
  const value=token[0];if(value[0]!=='<'){stack[stack.length-1].children.push(value);continue;}
  const end=value.match(/^<\/([\w:-]+)/);if(end){let index=stack.length-1;while(index>0&&stack[index].tag!==end[1].toLowerCase())index--;if(index>0)stack.length=index;continue;}
  const start=value.match(/^<([\w:-]+)/);if(!start)continue;
  const attrs={};for(const a of value.matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g))attrs[a[1].toLowerCase()]=listingDecode(a[2]??a[3]??a[4]);
  const parent=stack[stack.length-1],node={tag:start[1].toLowerCase(),attrs,children:[],parent};parent.children.push(node);nodes.push(node);
  if(nodes.length>100000)throw Error('Слишком много элементов HTML');
  if(!empty.has(node.tag)&&!value.endsWith('/>'))stack.push(node);
 }
 return {root,nodes:nodes.filter(n=>{for(let p=n;p;p=p.parent)if(listingExcluded(p))return false;return true;})};
}
