"""Bounded operator-only tabular snapshots. No database, original files or network.

The authenticated caller supplies an allowlisted projection of library rows.
XLSX is plain OOXML with typed cells, no macros, external links or user formulas.
"""
import csv
from decimal import Decimal, ROUND_HALF_UP
import io
import json
import re
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

MAX_ROWS = 5000
MAX_REQUEST_BYTES = 8_000_000
FIELDS = frozenset(('id','title','url','district','address','metro','rooms','area','floor','floors','price','pricePeriod','priceCurrency','deposit','depositCurrency','commission','commissionAmount','commissionCurrency','commissionStatus','prepayMonths','utilities','status','oneTimeFees'))
COLUMNS = (
    ('id','ID'),('title','Заголовок'),('url','Источник'),('district','Район'),('address','Адрес'),('metro','Метро'),
    ('rooms','Комнаты'),('area','Площадь, м²'),('floor','Этаж'),('floors','Этажей'),
    ('price','Арендная ставка'),('pricePeriod','Период ставки'),('priceCurrency','Валюта аренды'),
    ('deposit','Залог'),('depositCurrency','Валюта залога'),('commission','Комиссия, % · внутри команды'),
    ('commissionAmount','Комиссия фиксированной суммой'),('commissionCurrency','Валюта фиксированной комиссии'),
    ('prepayMonths','Предоплата, месяцев'),('otherFees','Прочие известные сборы, ₽'),
    ('firstSubtotal','Известная часть первого платежа, ₽'),('firstTotal','Полный первый платёж, ₽'),
    ('paymentStatus','Полнота расчёта'),('status','Рабочий статус'),
)
_STATES = {'new':'Новый','verified':'Проверен','ready':'Готов','published':'Опубликован','archive':'Архив'}
_PERIODS = {'month':'Месяц','day':'Сутки','week':'Неделя','year':'Год','unknown':'Неизвестно'}
_PHONE = re.compile(r'(?<!\d)(?:\+?7|8)[\s().-]*(?:\d[\s().-]*){10}(?!\d)|\+[1-9](?:[\s().-]*\d){8,14}(?!\d)')
_FORMULA = re.compile(r'^[\s\ufeff\u200b-\u200f\u202a-\u202e\u2060-\u206f]*[=+\-@]')
_FEE = re.compile(r'комисси|агентск|commission|中介|佣金', re.I)


def _text(value, maximum, label):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value)>maximum or re.search(r'[\ud800-\udfff\ufffe\uffff]',value):
        raise ValueError('Неверное поле выгрузки: '+label)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    return _PHONE.sub('[телефон скрыт]', text)


def _number(value, label, *, maximum=Decimal('1000000000'), places=2, integer=False):
    if value in (None, ''):
        return None
    if type(value) not in (int, float, str):
        raise ValueError('Неверное число выгрузки: '+label)
    text = re.sub(r'[\s\u00a0\u202f]', '', str(value)).replace(',', '.')
    expression = r'\d+' if integer else r'\d+(?:\.\d{1,'+str(places)+'})?'
    if not re.fullmatch(expression, text):
        raise ValueError('Неверное число выгрузки: '+label)
    number = Decimal(text)
    if number>maximum:
        raise ValueError('Число вне диапазона: '+label)
    return int(number) if integer else number


def _enum(value, choices, default, label):
    selected = default if value is None or value=='' else value
    if not isinstance(selected, str) or selected not in choices:
        raise ValueError('Неверная единица или категория: '+label)
    return selected


def _source(value):
    text = _text(value, 2048, 'источник')
    if not text:
        return ''
    try:
        url = urlsplit(text)
        host = (url.hostname or '').lower()
        if url.scheme!='https' or url.username or url.password or url.port not in (None,443):
            return ''
        if (re.fullmatch(r'(?:[a-z0-9-]+\.)*cian\.ru', host) and re.fullmatch(r'/rent/(?:flat|room)/\d+/?', url.path)
                or re.fullmatch(r'(?:[a-z0-9-]+\.)*avito\.ru', host) and re.search(r'/(?:kvartiry|komnaty)/[^/]+_\d+/?$', url.path)):
            return urlunsplit(('https',host,url.path,'',''))
    except ValueError:
        pass
    return ''


def normalize_row(value):
    if not isinstance(value, dict) or set(value)-FIELDS:
        raise ValueError('Выгрузка принимает только разрешённые поля без заметок и исходников')
    row = {key:_text(value.get(key),maximum,key) for key,maximum in (('id',120),('title',300),('district',200),('address',500),('metro',200))}
    if not row['id']:
        raise ValueError('Нужен ID карточки')
    row['url'] = _source(value.get('url'))
    for key in ('price','deposit','commissionAmount'):
        row[key] = _number(value.get(key),key)
    row['commission'] = _number(value.get('commission'),'commission',maximum=Decimal('1000'),places=4)
    row['area'] = _number(value.get('area'),'area',maximum=Decimal('1000000'))
    for key,maximum in (('rooms',100),('floor',10000),('floors',10000)):
        row[key] = _number(value.get(key),key,maximum=Decimal(maximum),integer=True)
    row['prepayMonths'] = _number(value.get('prepayMonths'),'prepayMonths',maximum=Decimal('36'),integer=True)
    if row['prepayMonths'] is None:
        row['prepayMonths'] = 1
    elif row['prepayMonths']<1:
        raise ValueError('Предоплата должна быть от 1 до 36 месяцев')
    row['pricePeriod'] = _enum(value.get('pricePeriod'),_PERIODS,'month','pricePeriod')
    for key in ('priceCurrency','depositCurrency','commissionCurrency'):
        row[key] = _enum(value.get(key),('RUB','USD','EUR','unknown'),'RUB',key)
    row['commissionStatus'] = _enum(value.get('commissionStatus'),('present','none','unknown'),'unknown','commissionStatus')
    row['status'] = _enum(value.get('status'),_STATES,'new','status')
    row['utilities'] = _enum(value.get('utilities'),('included','meters','extra','unknown'),'unknown','utilities')
    fees = value.get('oneTimeFees',[])
    if not isinstance(fees,list) or len(fees)>30:
        raise ValueError('Допускается до 30 разовых сборов')
    row['oneTimeFees'] = []
    for fee in fees:
        if not isinstance(fee,dict) or set(fee)-{'label','amount','currency'}:
            raise ValueError('Неверный разовый сбор')
        row['oneTimeFees'].append({'label':_text(fee.get('label'),150,'сбор'),'amount':_number(fee.get('amount'),'сумма сбора'),'currency':_enum(fee.get('currency'),('RUB','USD','EUR','unknown'),'RUB','валюта сбора')})
    return row


def initial_payment(row):
    lines = []
    unknown = []
    unit_ok = row['pricePeriod']=='month' and row['priceCurrency']=='RUB'
    rent = row['price']
    if rent is None or rent<=0:
        unknown.append('аренда')
    elif unit_ok:
        lines.append(rent*row['prepayMonths'])
    else:
        unknown.append('период/валюта аренды')
    deposit = row['deposit']
    if deposit is None:
        unknown.append('залог')
    elif deposit==0 or row['depositCurrency']=='RUB':
        lines.append(deposit)
    else:
        unknown.append('валюта залога')
    fixed = row['commissionAmount']
    fixed_ok = fixed is None or fixed==0 or row['commissionCurrency']=='RUB'
    percentage = ((rent*row['commission']/100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
                  if rent is not None and unit_ok and row['commission'] is not None else None)
    fee = fixed if fixed is not None and fixed_ok else percentage if fixed_ok else None
    if fixed is not None and percentage is not None and fixed_ok and fixed!=percentage:
        fee = None
        unknown.append('противоречивая комиссия')
    if fee is None and fixed is None and row['commission'] is None and row['commissionStatus']=='none':
        fee = Decimal(0)
    if fee is None:
        unknown.append('комиссия')
    else:
        lines.append(fee)
    other = Decimal(0)
    for extra in row['oneTimeFees']:
        if _FEE.search(extra['label']):
            continue
        if extra['amount'] is None or extra['currency']!='RUB':
            unknown.append('разовые сборы')
        else:
            other += extra['amount']
    lines.append(other)
    subtotal = sum(lines,Decimal(0)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    return {'otherFees':other,'firstSubtotal':subtotal,'firstTotal':None if unknown else subtotal,'paymentStatus':'Требует уточнения' if unknown else 'Полный расчёт'}


def table_values(rows):
    result = []
    for row in rows:
        values = {**row,**initial_payment(row),'pricePeriod':_PERIODS[row['pricePeriod']],'status':_STATES[row['status']]}
        result.append([values.get(key) for key,_ in COLUMNS])
    return result


def csv_text(value):
    text = '' if value is None else format(value,'f') if isinstance(value,Decimal) else str(value)
    return "'"+text if isinstance(value,str) and _FORMULA.search(text) else text


def csv_bytes(rows):
    output=io.StringIO(newline='')
    writer=csv.writer(output,delimiter=';',lineterminator='\r\n',quoting=csv.QUOTE_ALL)
    writer.writerow([label for _,label in COLUMNS])
    for row in table_values(rows):
        writer.writerow([csv_text(value) for value in row])
    return ('\ufeff'+output.getvalue()).encode('utf-8')


def _column(index):
    result=''
    while index:
        index,part=divmod(index-1,26)
        result=chr(65+part)+result
    return result


def _cell(value,reference,style=0):
    if value is None:
        return '<c r="'+reference+'" s="'+str(style)+'"/>'
    if isinstance(value,(int,Decimal)) and not isinstance(value,bool):
        selected=style if style else 2 if isinstance(value,Decimal) else 0
        return '<c r="'+reference+'" s="'+str(selected)+'"><v>'+str(value)+'</v></c>'
    return '<c r="'+reference+'" s="'+str(style)+'" t="inlineStr"><is><t xml:space="preserve">'+escape(str(value))+'</t></is></c>'


def xlsx_bytes(rows):
    values=table_values(rows)
    widths=[19,38,46,23,46,26,12,15,10,10,18,15,16,18,16,22,24,23,18,23,26,26,46,19]
    last=_column(len(COLUMNS));end=max(4,len(values)+4)
    body=['<row r="1" ht="28" customHeight="1">'+_cell('CILIEV · внутренняя библиотека объектов','A1',1)+'</row>',
          '<row r="2" ht="38" customHeight="1">'+_cell('Внутренний снимок с комиссией. Не клиентский документ. Коммунальные услуги не входят в первый платёж; при изменении данных сформируйте выгрузку заново.','A2',4)+'</row>',
          '<row r="4" ht="44" customHeight="1">'+''.join(_cell(label,_column(index)+'4',3) for index,(_,label) in enumerate(COLUMNS,1))+'</row>']
    for number,row in enumerate(values,5):
        lines=max(2,max((len(str(value))*1.35/max(widths[index]-2,8)+1) for index,value in enumerate(row) if value is not None))
        height=min(300,round(lines*15))
        body.append('<row r="'+str(number)+'" ht="'+str(height)+'" customHeight="1">'+''.join(_cell(value,_column(index)+str(number),5 if index==16 else 0) for index,value in enumerate(row,1))+'</row>')
    sheet='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:'+last+str(end)+'"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="20"/><cols>'+''.join('<col min="'+str(i)+'" max="'+str(i)+'" width="'+str(width)+'" customWidth="1"/>' for i,width in enumerate(widths,1))+'</cols><sheetData>'+''.join(body)+'</sheetData><autoFilter ref="A4:'+last+str(end)+'"/><mergeCells count="2"><mergeCell ref="A1:'+last+'1"/><mergeCell ref="A2:'+last+'2"/></mergeCells><pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.2" footer="0.2"/><pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>'
    styles='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="0.####"/></numFmts><fonts count="4"><font><sz val="11"/><color rgb="FF1C293A"/><name val="Calibri"/></font><font><b/><sz val="16"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFE0C78B"/><name val="Calibri"/></font><font><sz val="11"/><color rgb="FF626C7A"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF10243B"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf><xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    files={
        '[Content_Types].xml':'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels':'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Объекты" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml':styles,'xl/worksheets/sheet1.xml':sheet,
    }
    output=io.BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED,compresslevel=6) as archive:
        for name,text in files.items():
            archive.writestr(name,text.encode('utf-8'))
    return output.getvalue()


def export_table(data):
    if not isinstance(data,dict) or set(data)!={'format','rows'} or data['format'] not in ('csv','xlsx'):
        raise ValueError('Нужен формат csv/xlsx и массив разрешённых строк')
    if not isinstance(data['rows'],list) or len(data['rows'])>MAX_ROWS:
        raise ValueError('Допускается до 5000 строк')
    if len(json.dumps(data,ensure_ascii=False).encode('utf-8'))>MAX_REQUEST_BYTES:
        raise ValueError('Выгрузка превышает 8 МБ исходных полей')
    rows=[normalize_row(row) for row in data['rows']]
    if len({row['id'] for row in rows})!=len(rows):
        raise ValueError('ID строк выгрузки должны быть уникальны')
    if data['format']=='csv':
        return csv_bytes(rows),'text/csv; charset=utf-8','redcn-operator-library.csv'
    return xlsx_bytes(rows),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','redcn-operator-library.xlsx'
