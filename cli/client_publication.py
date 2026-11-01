"""Commission-free derived client projection. Never mutate a saved source/draft."""
import re

PUBLICATION_POLICY_VERSION='client-publication-v2'
_COMMISSION=re.compile(r'комисси[а-яё]*|агентск[а-яё]*\s+(?:вознагражден[а-яё]*|сбор[а-яё]*|услуг[а-яё]*)|вознагражден[а-яё]*\s+(?:агент[а-яё]*|риелтор[а-яё]*)|риелторск[а-яё]*\s+(?:сбор[а-яё]*|услуг[а-яё]*)|中介\s*(?:费|費)|佣金|傭金|免佣|经纪\s*(?:费|費)|經紀\s*(?:费|費)|代理\s*(?:费|費)|\bcommission\b|\b(?:broker(?:age)?|agent|agency)\s+fees?\b',re.I)
_PROTECTED=re.compile(r'(?:аренд[ауы]|арендная\s+плата|залог|депозит|предоплат|аванс|коммунал|жку|квартплат|сч[её]тчик|площад|комнат|спальн|мебел|диван|кроват|холодильник|квартир|адрес|метро|этаж|\b(?:rent|deposit|utilities|bedrooms?)\b|租金|月租|押金|定金|预付|預付|物业|物業|水电|水電|面积|面積|卧室|臥室|家具|公寓|精装|精裝|平米|[一两兩二三四五六七八九十]室|[一两兩二三四五六七八九十]厅|地铁|地鐵|\d\s*(?:㎡|м²|м2))',re.I)
_PRIVATE=re.compile(r'(?:commission|^(?:clientFee|agentFee|brokerFee|agencyFee|initialPayment|paymentCalculation|oneTimeFees|workflow|factLedger|factRevision|factRevisions|serviceChecks|serviceConfirmations|serviceBenefits|publicationApproval|approvals|offer|bargainTerms|source(?:Audit|Description|Ru|Html)?|raw(?:Text|Html|Body|Description)?|original(?:Text|Html|Body|Description)|notes|operatorNotes|internal|rules|prefs|sourceEnvelope)$)',re.I)
_FORMAT=re.compile(r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]')
_FEE_GRAMMAR=re.compile(r'без|нет|не|комисси[а-яё]*|агентск[а-яё]*|вознагражден[а-яё]*|риелторск[а-яё]*|услуг[а-яё]*|сбор[а-яё]*|оплат[а-яё]*|плат[а-яё]*|отсутств[а-яё]*|взима[а-яё]*|составля[а-яё]*|дополнительн[а-яё]*|услови[а-яё]*|уточн[а-яё]*|обсужда[а-яё]*|оговор[а-яё]*|согласова[а-яё]*|разов[а-яё]*|единоразов[а-яё]*|руб(?:л[а-яё]*)?|процент[а-яё]*|посредник[а-яё]*|арендатор[а-яё]*|собственник[а-яё]*|нанимател[а-яё]*|(?:^|\s)(?:с|со|за|от|для|по|к|на|и|или|до|в|из|у)(?=\s|$)|no|zero|free|waived|payable|paid|pay|by|the|tenant|landlord|fees?|commission|broker(?:age)?|agency|agent|additional|negotiable|contact|details|discuss|request|one[ -]?time|rur|rub|rmb|中介|费|費|佣金|傭金|免佣|经纪|經紀|代理|无需|無需|支付|免|无|無|不收|不另收|收取|另付|另计|另計|费用|費用|服务|服務|元|卢布|盧布|人民币|人民幣|一次性|为|為|是|可议|可議|面议|面議|咨询|諮詢|协商|協商|双方|租客|房东|房東|承担|承擔|待确认|待確認|具体详询|具體詳詢|请|請|私聊|详询|詳詢|确认|確認|联系|聯繫',re.I)
def _commission_only(text):return not re.search(r'[^\W\d_]',_FEE_GRAMMAR.sub('',_FORMAT.sub('',text)),re.UNICODE)


class PublicationReviewRequired(ValueError):
    code='PUBLICATION_REVIEW_REQUIRED'
    def __init__(self,fragment,reason='Перед публикацией разделите комиссию и остальные условия'):
        self.fragment=fragment[:700]
        super().__init__(reason+': «'+fragment[:300]+'». Исходный текст сохранён.')

def contains_commission(value):return bool(_COMMISSION.search(_FORMAT.sub('',str(value or ''))))

def _clauses(line):
    if re.match(r'^\s*#',line):return [(text,' ') for text in re.findall(r'#[^#]*',line)]
    rows=[];start=0
    for index,char in enumerate(line):
        previous=line[index-1] if index else '';following=line[index+1] if index+1<len(line) else ''
        numeric=previous.isdigit() and following.isdigit()
        delimiter=char in ';；|｜·•。!?！？' or char==',' and not numeric or char=='，' or char=='.' and not numeric and (not following or following.isspace()) or char=='/' and previous.isspace() and following.isspace()
        if delimiter:rows.append((line[start:index],char));start=index+1
    rows.append((line[start:],''));return rows

def sanitize_text(value):
    if value is None:return ''
    if not isinstance(value,str):raise TypeError('Текст публикации должен быть строкой')
    if not contains_commission(value):return value
    lines=[]
    for line in value.replace('\r\n','\n').replace('\r','\n').split('\n'):
        if not contains_commission(line):lines.append(line);continue
        output='';separator=''
        for clause,delimiter in _clauses(line):
            text=clause.strip()
            if not text:continue
            if contains_commission(text):
                if _PROTECTED.search(_FORMAT.sub('',text)) or not _commission_only(text):raise PublicationReviewRequired(text)
                continue
            if output:output+=separator+(' ' if separator and not re.search(r'[\s。，；]',separator) else '')
            output+=text;separator=delimiter
        if output:
            if re.search(r'[。.!?！？]',separator):output+=separator
            lines.append(output)
    return '\n'.join(lines).strip()

def assert_public_text(value):
    cleaned=sanitize_text(value)
    if cleaned!=(value or ''):raise PublicationReviewRequired(value or '')
    return cleaned

_DISPUTED={key:re.compile(pattern,re.I) for key,pattern in {
    'deposit':r'押金|免押|залог|депозит','buildingType':r'кирпич|панел|монолит|блочн|砖|磚|板式|预制|預製|整体浇筑',
    'bathroom':r'浴缸|淋浴|ванн|душ','price':r'租金|月租|аренд[ауы]|арендная плата','area':r'面积|面積|площад|\d\s*(?:㎡|м²|м2)',
    'floor':r'楼层|樓層|этаж','rooms':r'комнат|卧室|臥室|[一两兩二三四五六七八九十]室'}.items()}
def _check_disputed(card):
    if not isinstance(card,dict):return
    fields=card.get('fields') if isinstance(card.get('fields'),dict) else card
    audit=fields.get('sourceAudit') or card.get('sourceAudit') or {}
    blocked=audit.get('blockedFields',[]) if isinstance(audit,dict) else []
    if not isinstance(blocked,list):return
    titles=card.get('titles') if isinstance(card.get('titles'),list) else []
    texts=[text for text in [card.get('title'),card.get('body'),card.get('tags'),*titles,fields.get('description')] if isinstance(text,str)]
    for field in blocked:
        if not isinstance(field,str):continue
        reason='Перед публикацией проверьте противоречивое поле '+field
        value=fields.get(field)
        if value is not None and value!='' and value!=[]:raise PublicationReviewRequired(field+': '+str(value),reason)
        expression=_DISPUTED.get(field)
        if expression is None:continue
        for text in texts:
            for line in re.split(r'\n|[。;]',text):
                if not expression.search(line):continue
                if field=='deposit' and not re.search(r'\d|无需|無需|免押|без\s+залога',line,re.I) and re.search(r'待确认|待確認|待核实|уточн|неизвест',line,re.I):continue
                raise PublicationReviewRequired(line.strip(),reason)

def client_card(card):
    _check_disputed(card)
    assert_card_services(card)
    fields=card.get('fields') if isinstance(card,dict) and isinstance(card.get('fields'),dict) else card
    if isinstance(fields,dict):
        def positive(value):
            try:return float(value)>0
            except (TypeError,ValueError,OverflowError):return False
        if positive(fields.get('price')):
            for key,unit in (('pricePeriod','month'),('priceCurrency','RUB')):
                if fields.get(key) and fields[key]!=unit:raise PublicationReviewRequired(key+': '+str(fields[key]),'Перед публикацией подтвердите месячную ставку в рублях')
        if positive(fields.get('deposit')) and fields.get('depositCurrency') and fields['depositCurrency']!='RUB':raise PublicationReviewRequired('depositCurrency: '+str(fields['depositCurrency']),'Перед публикацией подтвердите валюту залога')
    active=set()
    def project(value,depth=0):
        if depth>32:raise TypeError('Слишком глубокая модель публикации')
        if isinstance(value,str):
            if re.match(r'^data:image/(?:png|jpeg|webp);base64,',value,re.I):return value
            return sanitize_text(value)
        if not isinstance(value,(dict,list,tuple)):return value
        key=id(value)
        if key in active:raise TypeError('Циклическая модель публикации')
        active.add(key)
        if isinstance(value,(list,tuple)):result=[project(item,depth+1) for item in value]
        else:
            result={}
            for name,item in value.items():
                if _PRIVATE.search(str(name)):continue
                if name=='listing' and isinstance(item,dict):item={key:item[key] for key in ('url','images','latitude','longitude','sourceReadiness') if key in item}
                result[name]=project(item,depth+1)
        active.remove(key);return result
    projected=project(card or {})
    if isinstance(projected,dict):
        for key in ('body','title','tags'):
            if key in projected and not isinstance(projected[key],str):projected[key]=''
        if 'titles' in projected:
            titles=projected['titles'];titles=titles if isinstance(titles,list) else [titles] if isinstance(titles,str) else []
            projected['titles']=[value for value in titles if isinstance(value,str) and value.strip()]
        projected['publicationPolicyVersion']=PUBLICATION_POLICY_VERSION
    return projected


def assert_card_services(card, *, texts=None):
    from service_claims import assert_service_claims,ServiceClaimReview
    try:assert_service_claims(card,texts=texts)
    except ServiceClaimReview as error:raise PublicationReviewRequired(error.fragment,error.reason) from error
