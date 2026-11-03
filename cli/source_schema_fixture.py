"""Make inert structural fixtures from explicitly supplied source captures.

Never execute a page or retain its scripts, resources, URLs, contact text or
listing values. The retained tag hierarchy and bounded selector identifiers are
observed; every displayed field value and photo URL is fictional replacement.
"""
from html import escape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import plistlib
import re

ROOTS = {
 'cian': {'OfferTitleNew':'2-комн. квартира, 54 м², 5/17 эт.', 'OfferTitle':'2-комн. квартира, 54 м², 5/17 эт.',
  'Price':'80 000 ₽/мес.', 'PriceInfo':'80 000 ₽/мес.', 'PriceItem':'80 000 ₽/мес.',
  'Description':'Обезличенные тестовые значения: шкаф, диван и холодильник. Без сохранения текста исходного объявления.',
  'OfferSummaryInfoLayout':'Общая площадь 54 м². Кухня 12 м². Этаж 5 из 17.',
  'OfferSummaryInfoGroup':'Общая площадь 54 м². Кухня 12 м².', 'OfferFactItem':'Тестовая характеристика',
  'FeaturesLayout':'Мебель на кухне. Холодильник.', 'OfferGallery':'', 'GalleryInnerComponent':'',
  'AddressContainer':'Москва, Тестовая улица, 1', 'UndergroundList':'Тестовая · 10 минут пешком',
  'DeactivatedLabel':'Объявление снято с публикации'},
 'avito': {'item-view/title-info':'2-к. квартира, 54 м², 5/17 эт.',
  'item-view/item-price':'80 000 ₽/мес.','item-view/item-price-container':'80 000 ₽/мес.',
  'item-view/item-description':'Обезличенные тестовые значения: шкаф, диван и холодильник.',
  'item-view/item-params':'Комнат: 2. Общая площадь: 54 м². Кухня: 12 м². Этаж: 5 из 17.',
  'item-view/main-gallery':'', 'item-view/item-date':'Опубликовано в тестовую дату',
  'item-view/item-id':'Тестовый идентификатор удалён'}
}
SAFE_NAMES=set(ROOTS['cian'])|{'CardSectionNew','OfferSummaryInfoItem','ObjectFactoids','ObjectFactoidsItem','ThumbComponent','VerticalSlider','PaginationThumbsComponent','ButtonFullscreen','Scroller','Control','Geo','AddressItem','Underground','UndergroundItem','UndergroundIcon','OfferMetaData','WideOfferMainInfo','WideOfferRow','FeaturesItem','OfferSummaryInfoLayout'}
SAFE_TESTIDS={'title','price-amount','offer-facts','OfferSummaryInfoGroup','OfferSummaryInfoItem','metadata-updated-date','VerticalSliderContent','offers_history_deactivated_label'}
SAFE_MARKERS=set(ROOTS['avito'])|{'item-map-wrapper','item-map-button'}
SKIP_TAGS={'script','style','noscript','template','iframe','object','embed','svg','canvas','video','audio','source','link','meta','form','input','textarea','select','option'}
VOID={'img','br','hr','wbr','area','base','col','param','track','meta','link','input','source','embed'}
EXCLUDE=re.compile(r'Similar|Recommend|Suggest|OfferHistory|Agent|Author|Phone|Contact|Login|User|Messenger|seller-info|item-view-contacts|phone-button',re.I)

class Node:
 def __init__(self,tag='root',attrs=None,parent=None):self.tag=tag;self.attrs=dict(attrs or []);self.parent=parent;self.children=[]

class _Tree(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root]
 def handle_starttag(self,tag,attrs):
  node=Node(tag,attrs,self.stack[-1]);self.stack[-1].children.append(node)
  if tag not in VOID:self.stack.append(node)
 def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)
 def handle_endtag(self,tag):
  for index in range(len(self.stack)-1,0,-1):
   if self.stack[index].tag==tag:self.stack=self.stack[:index];break
 # Raw text, comments and scripts are deliberately never stored in the tree.


def _selector(node,platform):return node.attrs.get('data-name' if platform=='cian' else 'data-marker','')
def _excluded(node):
 while node:
  if node.tag in SKIP_TAGS or any(EXCLUDE.search(str(node.attrs.get(key,''))) for key in ('data-name','data-marker','data-testid')):return True
  node=node.parent
 return False


def sanitize_html(html):
 if not isinstance(html,str) or len(html)>12_000_000:raise ValueError('Capture must be HTML text up to 12 MB')
 parser=_Tree();parser.feed(html);all_nodes=[]
 def visit(node):
  for child in node.children:all_nodes.append(child);visit(child)
 visit(parser.root)
 counts={platform:sum(_selector(node,platform) in values and not _excluded(node) for node in all_nodes) for platform,values in ROOTS.items()}
 platform=max(counts,key=counts.get)
 if counts[platform]<3:raise ValueError('No recognized source-card schema; a challenge page is not a fixture')
 selected=[]
 for node in all_nodes:
  if _selector(node,platform) not in ROOTS[platform] or _excluded(node):continue
  ancestor=node.parent
  while ancestor and _selector(ancestor,platform) not in ROOTS[platform]:ancestor=ancestor.parent
  if ancestor is None:selected.append(node)
 photo=[0];tag_count=[0]
 def render(node,root=False):
  if _excluded(node):return ''
  tag=node.tag if re.fullmatch(r'[a-z][a-z0-9-]*',node.tag) else 'div';attrs={};tag_count[0]+=1
  for key,value in node.attrs.items():
   if key=='data-name' and value in SAFE_NAMES or key=='data-marker' and value in SAFE_MARKERS or key=='data-testid' and value in SAFE_TESTIDS or key=='itemprop' and value in {'image','name','description','price','priceCurrency','address'}:attrs[key]=value
  if tag=='img':photo[0]+=1;attrs['src']='https://fixture.invalid/photo-%03d.jpg'%photo[0];attrs['alt']='Тестовое изображение'
  text=ROOTS[platform].get(_selector(node,platform),'')
  start='<'+tag+''.join(' '+key+'="'+escape(value,quote=True)+'"' for key,value in sorted(attrs.items()))+'>'
  return start+(('' if tag in VOID else escape(text)+''.join(render(child) for child in node.children)+'</'+tag+'>'))
 rendered=[render(node,True) for node in selected]
 result='<!doctype html>\n<html><head><meta charset="utf-8"><title>Обезличенный структурный снимок</title></head><body data-fixture-kind="anonymized-observed-dom">\n'+'\n'.join(rendered)+'\n</body></html>\n'
 metadata={'platform':platform,'observedSections':[_selector(node,platform) for node in selected],'retainedTags':tag_count[0],'replacementPhotos':photo[0],'valuesAreFictional':True,'scriptsRetained':0,'externalOriginalResourcesRetained':0,'deactivatedMarkerObserved':any(_selector(node,platform)=='DeactivatedLabel' and not _excluded(node) for node in all_nodes),'sanitizedSha256':hashlib.sha256(result.encode()).hexdigest()}
 return result,metadata


def from_file(path):
 path=Path(path);raw=path.read_bytes()
 if len(raw)>60_000_000:raise ValueError('Capture exceeds 60 MB')
 if path.suffix.lower()=='.webarchive':
  # Only WebMainResource.WebResourceData is used. Headers and subresources are
  # never inspected or exported, and no URL is opened.
  value=plistlib.loads(raw);main=value.get('WebMainResource',{}).get('WebResourceData')
  if not isinstance(main,(bytes,str)):raise ValueError('Webarchive has no main HTML')
  html=main.decode('utf-8',errors='replace') if isinstance(main,bytes) else main;kind='webarchive-main-html'
 else:html=raw.decode('utf-8',errors='replace');kind='supplied-html'
 cleaned,metadata=sanitize_html(html)
 return cleaned,{**metadata,'inputFormat':kind,'sourceSha256':hashlib.sha256(raw).hexdigest(),'mainHtmlSha256':hashlib.sha256(html.encode()).hexdigest(),'permission':'User supplied this capture and explicitly requested source analysis and development; only anonymized structural derivatives are retained.'}


def browser_capture_script():
 config={'roots':ROOTS,'names':sorted(SAFE_NAMES),'markers':sorted(SAFE_MARKERS),'testids':sorted(SAFE_TESTIDS),'skip':sorted(SKIP_TAGS),'void':sorted(VOID)}
 return 'async () => { const cfg='+json.dumps(config,ensure_ascii=False)+r''';
 const exclude=/Similar|Recommend|Suggest|OfferHistory|Agent|Author|Phone|Contact|Login|User|Messenger|seller-info|item-view-contacts|phone-button/i;
 const nodes=[...document.querySelectorAll('[data-name],[data-marker]')];
 const key=p=>p==='cian'?'data-name':'data-marker',name=(n,p)=>n.getAttribute(key(p))||'';
 const excluded=n=>{for(let x=n;x;x=x.parentElement)if(cfg.skip.includes(x.tagName.toLowerCase())||['data-name','data-marker','data-testid'].some(k=>exclude.test(x.getAttribute(k)||'')))return true;return false;};
 const counts=Object.fromEntries(Object.keys(cfg.roots).map(p=>[p,nodes.filter(n=>Object.hasOwn(cfg.roots[p],name(n,p))&&!excluded(n)).length]));
 const platform=counts.cian>=counts.avito?'cian':'avito';if(counts[platform]<3)throw Error('No recognized source-card schema; do not capture a challenge page');
 const roots=nodes.filter(n=>Object.hasOwn(cfg.roots[platform],name(n,platform))&&!excluded(n)&&!(()=>{for(let a=n.parentElement;a;a=a.parentElement)if(Object.hasOwn(cfg.roots[platform],name(a,platform)))return true;return false;})());
 const esc=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');let photos=0,tags=0;
 const render=(n,root=false)=>{if(excluded(n))return '';const tag=n.tagName.toLowerCase();if(!/^[a-z][a-z0-9-]*$/.test(tag))return '';tags++;const attrs={};for(const k of ['data-name','data-marker','data-testid']){const v=n.getAttribute(k);if((k==='data-name'?cfg.names:k==='data-marker'?cfg.markers:cfg.testids).includes(v))attrs[k]=v;}const item=n.getAttribute('itemprop');if(['image','name','description','price','priceCurrency','address'].includes(item))attrs.itemprop=item;if(tag==='img'){attrs.src='https://fixture.invalid/photo-'+String(++photos).padStart(3,'0')+'.jpg';attrs.alt='Тестовое изображение';}const start='<'+tag+Object.keys(attrs).sort().map(k=>' '+k+'="'+esc(attrs[k])+'"').join('')+'>';return start+(cfg.void.includes(tag)?'':esc(cfg.roots[platform][name(n,platform)]||'')+[...n.children].map(c=>render(c)).join('')+'</'+tag+'>');};
 const html='<!doctype html>\n<html><head><meta charset="utf-8"><title>Обезличенный структурный снимок</title></head><body data-fixture-kind="anonymized-observed-dom">\n'+roots.map(n=>render(n,true)).join('\n')+'\n</body></html>\n';
 const digest=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join('');
 return {html,metadata:{platform,observedSections:roots.map(n=>name(n,platform)),retainedTags:tags,replacementPhotos:photos,valuesAreFictional:true,scriptsRetained:0,externalOriginalResourcesRetained:0,deactivatedMarkerObserved:nodes.some(n=>name(n,platform)==='DeactivatedLabel'&&!excluded(n)),sanitizedSha256:await digest(html),sourceDomSha256:await digest(document.documentElement.outerHTML),inputFormat:'browser-observed-dom',permission:'User explicitly requested ordinary source inspection and anonymized structural fixtures.'}};
}'''
