"""Independent authored development probes; never add these to training data.

Passing checks is a factual/format screen, not a fluency or native review. The
probe version and content hash belong in every measurement so runs are paired.
"""
import json

from text_quality import LISTING_SYSTEM, TRANSLATION_SYSTEM

TRANSLATIONS = [
 ('ru-zh-equipment-negation','ru-zh','Есть холодильник и микроволновая печь, но нет посудомоечной машины.',
  '配有冰箱和微波炉，但没有洗碗机。'),
 ('ru-zh-unconfirmed','ru-zh','Наличие кондиционера не подтверждено. Диван есть.',
  '空调配置尚未确认。已配备沙发。'),
 ('ru-zh-numeric-units','ru-zh','Аренда 88300 рублей в месяц, площадь 47,2 м², квартира на 7 этаже.',
  '月租88300卢布，面积47.2㎡，公寓位于7楼。'),
 ('ru-zh-registration-conditional','ru-zh','Временная регистрация возможна только после согласования.',
  '临时居住登记需协商确认。'),
 ('ru-zh-permissions','ru-zh','Можно с детьми. С животными нельзя.',
  '可带孩子入住。不允许携带宠物入住。'),
 ('ru-zh-rent-deposit','ru-zh','Квартира без мебели. Залог 42000 рублей, аренда 76000 рублей в месяц.',
  '公寓无家具。押金42000卢布，月租76000卢布。'),
 ('ru-zh-transit','ru-zh','До метро 11 минут пешком. Автобус 2 идёт до остановки Парк.',
  '步行到地铁需11分钟。乘坐2路公交可到公园站。'),
 ('zh-ru-equipment-negation','zh-ru','公寓配有洗衣机，但没有电视。',
  'В квартире есть стиральная машина, но нет телевизора.'),
 ('zh-ru-unconfirmed','zh-ru','冰箱配置尚未确认。衣柜已配备。',
  'Наличие холодильника не подтверждено. Шкаф есть.'),
 ('zh-ru-numeric-units','zh-ru','月租91200卢布，面积51.5平方米，位于16楼。',
  'Аренда 91200 рублей в месяц, площадь 51,5 м², квартира на 16 этаже.'),
 ('zh-ru-conditional','zh-ru','宠物入住需经房东同意，不可直接入住。',
  'Проживание с животными возможно по согласованию с собственником; без этого заселяться нельзя.'),
 ('zh-ru-kitchen','zh-ru','厨房有烤箱和微波炉，房间没有电脑。',
  'На кухне есть духовка и микроволновая печь, в комнате нет компьютера.'),
 ('zh-ru-bed-negation','zh-ru','没有双人床，只有沙发。',
  'Нет двуспальной кровати, есть только диван.'),
 ('zh-ru-rent-deposit','zh-ru','月租68000卢布，押金34000卢布。',
  'Аренда 68000 рублей в месяц, залог 34000 рублей.'),
]
LISTINGS = [
 {'id':'json-price-area','reference':'莫斯科公寓，46.7㎡，月租83800卢布。配有冰箱和书桌。',
  'fields':{'price':'83800','area':'46.7','priceCurrency':'RUB'},
  'referenceAnswer':{'title':'莫斯科46.7㎡｜83800卢布/月','body':'冰箱和书桌已配备。面积46.7㎡，月租83800卢布。'}},
 {'id':'json-unknown-equipment','reference':'莫斯科公寓，43.5㎡，月租77500卢布。沙发已确认，洗碗机是否配备尚未确认。',
  'fields':{'price':'77500','area':'43.5','priceCurrency':'RUB'},
  'referenceAnswer':{'title':'莫斯科43.5㎡｜77500卢布/月','body':'沙发已确认，洗碗机配置仍需核实。43.5㎡，月租77500卢布。'}},
 {'id':'json-two-bedrooms','reference':'莫斯科公寓，61㎡，月租105500卢布。有两间独立卧室、餐桌和冰箱。',
  'fields':{'price':'105500','area':'61','priceCurrency':'RUB'},
  'referenceAnswer':{'title':'莫斯科61㎡独立双卧','body':'两间卧室独立，配有餐桌和冰箱。面积61㎡，月租105500卢布。'}},
 {'id':'json-negative-equipment','reference':'莫斯科公寓，33.5㎡，月租61500卢布。配有床和洗衣机，未配置空调。',
  'fields':{'price':'61500','area':'33.5','priceCurrency':'RUB'},
  'referenceAnswer':{'title':'莫斯科33.5㎡｜61500卢布/月','body':'床和洗衣机已配备，未配空调。33.5㎡，月租61500卢布。'}},
]


def probes():
    result=[]
    for identifier,direction,source,answer in TRANSLATIONS:
        result.append({'id':identifier,'task':direction,'source':source,'referenceAnswer':answer,
            'messages':[{'role':'system','content':TRANSLATION_SYSTEM.format(language='Simplified Chinese' if direction=='ru-zh' else 'Russian')},
                {'role':'user','content':json.dumps({'text':source,'approvedGlossary':[]},ensure_ascii=False)}]})
    for row in LISTINGS:
        request={'verifiedText':row['reference'],'style':'自然简洁，少用表情，用短段落',
            'previousTextToAvoid':'','approvedStyleExamples':[],'variation':'quality-'+row['id']}
        result.append({**row,'task':'listing','messages':[{'role':'system','content':LISTING_SYSTEM},
            {'role':'user','content':json.dumps(request,ensure_ascii=False)}]})
    return result
