"""Observe source-contract drift without inventing absent listing values.

The only retained source excerpt is bounded visible text with common contact
and credential patterns redacted. It belongs to private review; publication and
diagnostic packages must exclude it. Unknown attributes/scripts are hashed or
discarded rather than copied into the report.
"""
from html.parser import HTMLParser
import hashlib
import math
import re
from urllib.parse import urlsplit

VERSION='source-schema-health-v1'
CRITICAL=('price','area','layout','description')
KNOWN={
    'cian':{'OfferTitleNew','OfferTitle','Price','PriceInfo','Description','OfferDescription',
            'OfferSummaryInfoItem','OfferSummaryInfoGroup','OfferSummaryInfoLayout','ObjectFactoidsItem',
            'OfferFactItem','FeaturesItem','GeneralInfoSection','OfferGallery','Gallery','Underground'},
    'avito':{'item-view/title-info','item-view/item-price','item-view/item-price-container',
             'item-view/item-description','item-view/item-params','item-view/main-gallery'},
}
LABELS={'price':'цена','area':'площадь','layout':'планировка','description':'описание'}
REASONS={'unknown-schema':'Форма страницы не соответствует известному контракту источника.',
         'critical-fields-missing':'Не все обязательные сведения извлечены; проверьте исходный текст.',
         'invalid-critical-value':'Источник вернул неоднозначное или недопустимое значение.',
         'field-conflict':'Источник содержит противоречивые сведения.',
         'no-photo-evidence':'Фотографии источника не подтверждены.'}


class Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.skip=0;self.parts=[];self.markers=set();self.jsonld=False
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag in {'script','style','noscript','template','iframe','object','svg'}:
            self.skip+=1
            if tag=='script' and values.get('type','').lower()=='application/ld+json':self.jsonld=True
        if not self.skip:
            for name in ('data-name','data-marker'):
                value=values.get(name,'')
                if re.fullmatch(r'[A-Za-z0-9_/-]{1,100}',value):self.markers.add(value)
    def handle_endtag(self,tag):
        if tag in {'script','style','noscript','template','iframe','object','svg'} and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip and len(self.parts)<1000:self.parts.append(data[:1000])


def private_fragment(markup):
    tree=Visible();tree.feed(markup)
    text=re.sub(r'\s+',' ',' '.join(tree.parts)).strip()
    # Keep no page URLs, email addresses, social handles or phone-shaped strings.
    text=re.sub(r'https?://\S+|www\.\S+','[ссылка удалена]',text,flags=re.I)
    text=re.sub(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b|(?<!\w)@[A-Za-z0-9_]{3,}', '[контакт удалён]', text)
    text=re.sub(r'(?<!\w)(?:\+?\d[\s().-]*){10,15}(?!\d)','[телефон удалён]',text)
    text=re.sub(r'(?i)\b(?:token|password|passwd|secret|authorization|api[_ -]?key|пароль|токен)\s*[:=]\s*\S+', '[секрет удалён]', text)
    text=re.sub(r'\b[A-Za-z0-9_-]{32,}\b','[идентификатор удалён]',text)
    return text[:700],tree


def positive(value):
    if isinstance(value,bool):return False
    try:number=float(str(value).replace(',','.'))
    except (TypeError,ValueError):return False
    return math.isfinite(number) and number>0


def evaluate(markup,listing,base_url=''):
    if not isinstance(markup,str) or len(markup.encode())>8_000_000 or not isinstance(listing,dict):
        raise ValueError('Bounded HTML and parsed listing required')
    fragment,tree=private_fragment(markup)
    try:host=(urlsplit(base_url).hostname or '').lower()
    except ValueError:host=''
    platform='avito' if host=='avito.ru' or host.endswith('.avito.ru') else 'cian' if host=='cian.ru' or host.endswith('.cian.ru') else 'unknown'
    markers=KNOWN.get(platform,set()) & tree.markers
    recognized=len(markers)>=2 or tree.jsonld
    fields=listing.get('fields') if isinstance(listing.get('fields'),dict) else {}
    layout=fields.get('layout')
    coverage={'price':positive(fields.get('price')), 'area':positive(fields.get('area')),
              'layout':layout in {'studio','euro2','euro3','one','two','three','four','free'} or positive(fields.get('rooms')),
              'description':isinstance(listing.get('text'),str) and len(listing['text'].strip())>=20}
    missing=[key for key in CRITICAL if not coverage[key]]
    reasons=[]
    if not recognized:reasons.append('unknown-schema')
    if missing:reasons.append('critical-fields-missing')
    if any(key in fields and fields[key] not in ('',None) and not coverage[key] for key in ('price','area')):reasons.append('invalid-critical-value')
    audit=fields.get('sourceAudit') or listing.get('sourceAudit') or {}
    if isinstance(audit,dict) and audit.get('blockedFields'):reasons.append('field-conflict')
    if not isinstance(listing.get('images'),list) or not listing['images']:reasons.append('no-photo-evidence')
    state='unsupported' if not recognized else 'partial' if missing or any(x in reasons for x in ('invalid-critical-value','field-conflict')) else 'recognized'
    return {'version':1,'contractVersion':VERSION,'platform':platform,'state':state,
            'criticalCoverage':coverage,'missingCritical':missing,'reasons':reasons,
            'recognizedMarkerCount':len(markers),'jsonLdObserved':tree.jsonld,
            'structureSha256':hashlib.sha256('\n'.join(sorted(tree.markers)).encode()).hexdigest(),
            'sourceSha256':hashlib.sha256(markup.encode()).hexdigest(),
            'privateReviewFragment':fragment,'privateReviewOnly':True,
            'unknownDoesNotMeanAbsent':True}


def public_summary(value):
    if not isinstance(value,dict) or value.get('version')!=1 or value.get('state') not in ('recognized','partial','unsupported'):return None
    reasons=[key for key in value.get('reasons',[]) if key in REASONS] if isinstance(value.get('reasons'),list) else []
    missing=[key for key in value.get('missingCritical',[]) if key in CRITICAL] if isinstance(value.get('missingCritical'),list) else []
    coverage=value.get('criticalCoverage',{})
    return {'version':1,'state':value['state'],'platform':value.get('platform') if value.get('platform') in ('cian','avito') else 'unknown',
            'reasons':reasons,'missingCritical':missing,
            'criticalCoverage':{key:coverage.get(key) is True for key in CRITICAL} if isinstance(coverage,dict) else {key:False for key in CRITICAL},
            'warnings':[REASONS[key] for key in reasons], 'unknownDoesNotMeanAbsent':True}


def attach(markup,listing,base_url=''):
    return {**listing,'sourceSchemaHealth':evaluate(markup,listing,base_url)}
