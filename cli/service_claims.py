"""Dated, card-bound agency claims at the final client boundary.

Neutral registration assistance is always permitted. Free or scoped promises
need the current operator receipt; no source statement creates that receipt.
"""
from datetime import datetime, timezone
import json
import math
import re
import copy
import unicodedata
from urllib.parse import urlsplit


class ServiceClaimReview(ValueError):
    def __init__(self, fragment, reason='Перед публикацией подтвердите актуальный объём услуги с датой и автором'):
        self.fragment=str(fragment)[:700]
        self.reason=reason
        super().__init__(reason)


def service_card_key(fields):
    if not isinstance(fields,dict):return ''
    try:
        url=urlsplit(fields.get('url',''))
        if url.scheme.lower() in ('http','https') and url.hostname and not url.username and not url.password:
            scheme=url.scheme.lower();host=url.hostname.lower();port=url.port
            if ':' in host:host='['+host+']'
            origin=scheme+'://'+host+(':'+str(port) if port and (scheme,port) not in (('http',80),('https',443)) else '')
            return 'url:'+(origin+(url.path or '/')).rstrip('/')
    except (ValueError,TypeError,AttributeError):pass
    for key in ('editorIdentity','resultId','id'):
        value=fields.get(key)
        if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',value):return key+':'+value
    return ''


def _positive(value):
    if type(value) not in (int,float,str):return ''
    try:number=float(value)
    except (ValueError,TypeError,OverflowError):return ''
    return str(int(number)) if math.isfinite(number) and number>0 and number.is_integer() else str(number) if math.isfinite(number) and number>0 else ''


def _signature(value):
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (ValueError,TypeError):return None


def service_claim_state(fields, *, now=None):
    if not isinstance(fields,dict):return {}
    current=datetime.now(timezone.utc).timestamp() if now is None else now
    if isinstance(current,datetime):current=current.timestamp()
    key=service_card_key(fields)
    scopes={'registration':{'service':'arrival-registration-assistance','months':_positive(fields.get('registrationMonths')),'people':_positive(fields.get('registrationPeople'))}}
    if fields.get('pickup') is True:scopes['pickup']={'included':True,'luggage':fields.get('luggage') is True}
    if fields.get('arrival') is True:scopes['arrival']={'included':True}
    benefits=fields.get('serviceBenefits','')
    if fields.get('support') is True and isinstance(benefits,str) and len(benefits)<=5000:scopes['support']={'included':True,'text':benefits}
    receipts=fields.get('serviceConfirmations')
    receipts=receipts if isinstance(receipts,dict) else {}
    state={}
    for name,value in scopes.items():
        row=receipts.get(name);valid=False
        if isinstance(row,dict):
            actor=row.get('actor');stamp=row.get('at')
            if isinstance(actor,str) and actor.strip() and len(actor.strip())<=100 and isinstance(stamp,str) and re.match(r'^\d{4}-\d\d-\d\dT',stamp) and re.search(r'(?:Z|[+-]\d\d:\d\d)$',stamp):
                try:
                    at=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
                    age=current-at
                    valid=bool(key) and row.get('cardKey')==key and type(current) in (int,float) and math.isfinite(current) and 0<=age<=30*86400 and _signature(row.get('value'))==_signature(value)
                except (ValueError,TypeError,OverflowError):pass
        state[name]=valid
    return state


_FORMAT=re.compile(r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]')
_FREE=r'(?:免费|免費|无偿|無償|бесплатн[а-яё]*|\bfree\b|\bcomplimentary\b)'
_REG=r'(?:抵达登记|临时居住登记|臨時居住登記|住宿登记|住宿登記|登记|登記|落地签|落地簽|注册|註冊|регистрац[а-яё]*|\bregistration\b)'
_PICKUP=r'(?:接送|接机|接機|接站|搬运行李|搬運行李|трансфер[а-яё]*|встреч[а-яё]*|достав[а-яё]*|\b(?:airport\s+)?pickup\b|\btransfers?\b|\bshuttle\b)'
_ARRIVAL=r'(?:抵达|抵達|到达|到達|手续|手續|прибыт[а-яё]*|процедур[а-яё]*|\barrival\b|\bprocedures?\b)'
_HEADER=re.compile(r'(?:🇨🇳\s*)?我们的优势\s*[:：]',re.I)
_REG_FREE=re.compile(_FREE+r'[^\n。.!?;]{0,45}'+_REG+'|'+_REG+r'[^\n。.!?;]{0,45}'+_FREE+r'|包落地',re.I)
_PICKUP_FREE=re.compile(_FREE+r'[^\n。.!?;]{0,45}'+_PICKUP+'|'+_PICKUP+r'[^\n。.!?;]{0,45}'+_FREE,re.I)
_ARRIVAL_FREE=re.compile(_FREE+r'[^\n。.!?;]{0,45}'+_ARRIVAL+'|'+_ARRIVAL+r'[^\n。.!?;]{0,45}'+_FREE,re.I)
_REG_SCOPE=re.compile(_REG+r'[^\n。.!?;]{0,150}',re.I)
_MONTHS=re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:个月|個月|месяц[а-яё]*|months?\b)',re.I)
_PEOPLE=re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:人|человек[а-яё]*|people\b|persons?\b)',re.I)


def _compact(value):
    # Match emoji:none styling and block/inline HTML wrapping while preserving
    # all ordinary letters, numbers and service wording in the approved scope.
    value=''.join(character for character in _FORMAT.sub('',value) if unicodedata.category(character)!='So' and character not in ('\ufe0f','\u200d'))
    return re.sub(r'\s+',' ',value).strip()


def assert_service_claims(card, *, texts=None, now=None):
    if not isinstance(card,dict):return
    fields=card.get('fields') if isinstance(card.get('fields'),dict) else card
    state=service_claim_state(fields,now=now)
    if texts is None:
        titles=card.get('titles') if isinstance(card.get('titles'),list) else [card.get('titles')]
        texts=[card.get('title'),card.get('body'),card.get('tags'),*titles,fields.get('description'),fields.get('details'),fields.get('highlight')]
    for raw in texts:
        if not isinstance(raw,str):continue
        text=_FORMAT.sub('',raw)
        # Assistance receipts do not authorize a free registration promise.
        if _REG_FREE.search(text):raise ServiceClaimReview(_REG_FREE.search(text)[0],'Бесплатная регистрация не подтверждается нейтральной услугой содействия')
        for scope in _REG_SCOPE.finditer(text):
            months=_MONTHS.findall(scope[0]);people=_PEOPLE.findall(scope[0])
            if months or people:
                if not state.get('registration') or any(_positive(value)!=_positive(fields.get('registrationMonths')) for value in months) or any(_positive(value)!=_positive(fields.get('registrationPeople')) for value in people):raise ServiceClaimReview(scope[0])
        # Full, exact benefits text has its own dated scope and can explicitly
        # include pickup. Removing it here cannot authorize extra appended text.
        compact=_compact(text)
        while match:=_HEADER.search(compact):
            benefits=_compact(fields.get('serviceBenefits','')) if isinstance(fields.get('serviceBenefits'),str) else ''
            rest=compact[match.end():].lstrip()
            if not state.get('support') or not benefits or not rest.startswith(benefits):raise ServiceClaimReview(compact[match.start():match.start()+700])
            compact=compact[:match.start()]+rest[len(benefits):]
        if _PICKUP_FREE.search(compact) and not state.get('pickup'):raise ServiceClaimReview(_PICKUP_FREE.search(compact)[0])
        if _ARRIVAL_FREE.search(compact) and not state.get('arrival'):raise ServiceClaimReview(_ARRIVAL_FREE.search(compact)[0])


def service_authorization(fields, *, now=None):
    """Public expiry only; never disclose receipt actors, values or card keys."""
    state=service_claim_state(fields,now=now);receipts=fields.get('serviceConfirmations',{}) if isinstance(fields,dict) else {}
    expiries=[]
    for name,valid in state.items():
        if valid:
            stamp=datetime.fromisoformat(receipts[name]['at'].replace('Z','+00:00')).timestamp()+30*86400
            expiries.append(stamp)
    if not expiries:return None
    return {'version':1,'expiresAt':datetime.fromtimestamp(min(expiries),timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')}


def safe_service_view(card):
    """Review-only derived view. Export checks must use the original card."""
    output=copy.deepcopy(card);fields=output.get('fields') if isinstance(output.get('fields'),dict) else output
    def clean(value, *, title=False):
        if not isinstance(value,str):return value
        try:assert_service_claims({'fields':fields,'body':value});return value
        except ServiceClaimReview:pass
        # Hide the whole obsolete benefits block, including non-free promises.
        value=re.sub(r'(?:🇨🇳\s*)?我们的优势\s*[:：][\s\S]*?(?=\n\s*\n|$)','',value)
        parts=[]
        # A decimal point is data, not a sentence boundary. Split explicit
        # title separators as well, so removing an obsolete free-service
        # promise cannot remove the rent, layout and station beside it.
        for clause in re.split(r'\n|[|｜]|(?<=[。!?；;])|(?<!\d)\.|\.(?!\d)',value):
            if not clause.strip():continue
            try:assert_service_claims({'fields':fields,'body':clause});parts.append(clause.strip())
            except ServiceClaimReview:
                if re.search(_REG,clause,re.I) and not _REG_FREE.search(clause):parts.append('抵达登记（临时居住登记）：提供办理协助。')
        return ('｜' if title else '\n').join(parts)
    for name in ('title','body','tags'):
        if name in output:output[name]=clean(output[name],title=name=='title')
    if isinstance(output.get('titles'),list):output['titles']=[clean(value,title=True) for value in output['titles']]
    for name in ('description','details','highlight'):
        if name in fields:fields[name]=clean(fields[name])
    return output

