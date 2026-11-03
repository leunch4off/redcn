#!/usr/bin/env python3
"""Freeze short, explicit assistant-authored rental lessons and a blind holdout.

These labels are authored by the development assistant after inspecting recent
source descriptions. They are neither operator approvals nor model self-labels.
No private listing, address, phone number or entire source ad is published.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from text_model_contract import validate_dataset
from text_quality import TRANSLATION_SYSTEM, LISTING_SYSTEM

TRAIN = [
 ('В комнате есть кровать.', '房间内配有床。'),
 ('В гостиной есть диван.', '客厅配有沙发。'),
 ('Диван раскладывается.', '沙发可以展开作为睡床使用。'),
 ('В спальне установлен шкаф для одежды.', '卧室配有衣柜。'),
 ('В комнате есть письменный стол.', '房间内配有书桌。'),
 ('На кухне есть обеденный стол и стулья.', '厨房配有餐桌和椅子。'),
 ('В кухне установлен холодильник.', '厨房配有冰箱。'),
 ('В квартире есть стиральная машина.', '公寓配有洗衣机。'),
 ('На кухне есть посудомоечная машина.', '厨房配有洗碗机。'),
 ('В кухне установлен духовой шкаф.', '厨房配有烤箱。'),
 ('В наличии микроволновая печь.', '配有微波炉。'),
 ('Установлена газовая плита.', '配有燃气灶。'),
 ('Установлена индукционная варочная панель.', '配有电磁炉。'),
 ('Над плитой есть вытяжка.', '炉灶上方配有抽油烟机。'),
 ('В гостиной установлен телевизор.', '客厅配有电视。'),
 ('В спальне есть кондиционер.', '卧室配有空调。'),
 ('В ванной комнате установлена ванна.', '浴室配有浴缸。'),
 ('В ванной комнате установлена душевая кабина.', '浴室配有淋浴间。'),
 ('Есть отдельная гардеробная.', '设有独立衣帽间。'),
 ('Есть отдельная кладовая.', '设有独立储藏室。'),
 ('Балкон застеклён.', '阳台已封闭安装玻璃窗。'),
 ('В квартире есть лоджия.', '公寓带有内凹式阳台。'),
 ('Комнаты изолированные.', '各房间相互独立。'),
 ('Кухня отдельная.', '厨房为独立空间。'),
 ('Кухня объединена с гостиной.', '厨房与客厅为一体式空间。'),
 ('Санузел раздельный.', '浴室与卫生间分开。'),
 ('Санузел совмещённый.', '浴室与卫生间在同一空间内。'),
 ('Окна выходят во двор.', '窗户朝向院内。'),
 ('Окна выходят на улицу.', '窗户朝向街道。'),
 ('В квартире требуется ремонт.', '公寓需要装修。'),
 ('В квартире сделан косметический ремонт.', '公寓已进行简单翻新。'),
 ('Дом включён в программу реновации и подлежит сносу.', '该楼已列入旧房改造计划，将被拆除。'),
 ('Можно проживать с детьми.', '允许携带孩子入住。'),
 ('Проживание с животными запрещено.', '不接受宠物入住。'),
 ('Проживание с животными возможно по согласованию.', '携带宠物需事先协商。'),
 ('Курение в квартире запрещено.', '公寓内禁止吸烟。'),
 ('Субаренда запрещена.', '禁止转租。'),
 ('Сдаётся на длительный срок.', '可长期租赁。'),
 ('Вода и электричество оплачиваются отдельно по счётчикам.', '水费和电费按表另付。'),
 ('Интернет оплачивается отдельно.', '网络费用另付。'),
 ('Залог можно внести двумя частями.', '押金可分两次支付。'),
 ('Показ квартиры по договорённости.', '看房时间需提前约定。'),
 ('Дата заселения пока не подтверждена.', '入住日期尚未确认。'),
 ('Владелец не оформляет временную регистрацию.', '房东不办理临时居住登记。'),
 ('Агентство помогает с временной регистрацией.', '中介可协助办理临时居住登记。'),
 ('Условия временной регистрации нужно уточнить.', '临时居住登记的办理条件需进一步确认。'),
 ('Наличие посудомоечной машины не подтверждено.', '是否配有洗碗机尚未确认。'),
 ('Холодильника в квартире нет.', '公寓内没有冰箱。'),
 ('Духовой шкаф отсутствует.', '未配备烤箱。'),
 ('Неизвестно, раскладывается ли диван.', '沙发是否可以展开作为睡床使用尚未确认。'),
 ('Время в пути до университета неизвестно.', '前往大学的通勤时间尚未确认。'),
 ('В здании есть пассажирский лифт.', '楼内设有客梯。'),
 ('Парковка во дворе открытая.', '院内为露天停车场。'),
 ('Въезд на парковку ограничен шлагбаумом.', '停车场入口设有道闸。'),
 ('В стоимость аренды не входит интернет.', '租金不包含网络费用。'),
 ('Можно обсудить скидку за небольшой ремонт.', '进行小规模维修可协商租金优惠。'),
]

VALID = [
 ('В комнате есть книжный стеллаж.', '房间内配有书架。'),
 ('В прихожей установлено зеркало.', '玄关配有镜子。'),
 ('На окнах есть плотные шторы.', '窗户配有厚窗帘。'),
 ('В спальне есть прикроватная тумба.', '卧室配有床头柜。'),
 ('Кровать оборудована матрасом.', '床配有床垫。'),
 ('Кухня оборудована мебелью.', '厨房配有橱柜。'),
 ('В квартире проведён интернет.', '公寓已接入网络。'),
 ('В доме нет лифта.', '楼内没有电梯。'),
 ('Ремонт не новый.', '装修并非全新。'),
 ('Дети старше 8 лет допускаются.', '允许8岁以上的孩子入住。'),
 ('Дом построен в 1963 году.', '该楼建于1963年。'),
 ('Высота потолков составляет 2.64 метра.', '层高为2.64米。'),
]

HELDOUT = [
 ('Квартира площадью 41.1 м² находится на 3 этаже.', '公寓面积为41.1㎡，位于3楼。'),
 ('Аренда составляет 70000 рублей в месяц, залог — 75000 рублей.', '月租为70000卢布，押金为75000卢布。'),
 ('Пешком до метро примерно 13 минут.', '步行到地铁站约需13分钟。'),
 ('В квартире два раскладных дивана.', '公寓内有两张可展开作睡床的沙发。'),
 ('Есть холодильник, но нет посудомоечной машины.', '配有冰箱，但没有洗碗机。'),
 ('Наличие кондиционера нужно уточнить.', '是否配有空调需进一步确认。'),
 ('Воду оплачивает жилец, отопление — собственник.', '水费由租客支付，供暖费由房东支付。'),
 ('Залог возвращается после выезда при соблюдении условий договора.', '遵守合同约定的情况下，退房后退还押金。'),
 ('Владелец делает временную регистрацию за отдельную плату.', '房东可办理临时居住登记，费用另付。'),
 ('Проживание с ребёнком возможно, с питомцами нельзя.', '可以携带孩子入住，但不接受宠物。'),
 ('Минимальный срок аренды — 11 месяцев.', '最短租期为11个月。'),
 ('В квартире нет мебели, кроме кухонного гарнитура.', '除厨房橱柜外，公寓没有其他家具。'),
]


def translations(pairs, prefix):
    rows = []
    for index, (ru, zh) in enumerate(pairs):
        for direction, source, target, language in [('ru-zh',ru,zh,'Simplified Chinese'), ('zh-ru',zh,ru,'Russian')]:
            rows.append({'id': f'{prefix}-{index:03d}-{direction}', 'groupId': f'{prefix}-lexical-{index:03d}',
                'status': 'project-authored', 'source': 'assistant-authored-rental-lesson-v30',
                'task': 'translation-'+direction, 'authorship': 'assistant-teacher',
                'nativeSpeakerReviewed': False, 'operatorApproved': False,
                'permission': {'trainingAllowed': prefix!='heldout', 'basis':'user-requested-assistant-teaching'},
                'messages': [{'role':'system','content':TRANSLATION_SYSTEM.format(language=language)},
                    {'role':'user','content':json.dumps({'text':source,'approvedGlossary':[]},ensure_ascii=False)},
                    {'role':'assistant','content':target}]})
    return rows


def ads(prefix, count, offset):
    rows = []
    equipment = [('书桌','письменный стол'),('洗衣机','стиральная машина'),('烤箱','духовой шкаф'),('沙发','диван')]
    for index in range(count):
        price = 53300+(index+offset)*1700
        area = 29+index+offset
        zh, ru = equipment[(index+offset)%len(equipment)]
        facts = f'面积{area}㎡。月租{price}卢布。已确认配有{zh}。水电按表另付。'
        title = [f'莫斯科租房｜{area}㎡公寓', f'{area}㎡莫斯科公寓｜月租{price}卢布', f'莫斯科房源｜配有{zh}'][index%3]
        body = [f'🏠 {area}㎡公寓，月租{price}卢布。\n配有{zh}，水电费用按表另付。',
                f'💰 月租{price}卢布，水电按表另付。\n这套{area}㎡公寓已确认配有{zh}。',
                f'📌 莫斯科公寓出租\n面积{area}㎡，配有{zh}。月租{price}卢布，水电按表另付。'][index%3]
        rows.append({'id':f'{prefix}-ad-{index:03d}','groupId':f'{prefix}-fictional-apartment-{index:03d}',
            'status':'project-authored','source':'assistant-authored-factual-ad-v30','task':'advertising',
            'authorship':'assistant-teacher','nativeSpeakerReviewed':False,'operatorApproved':False,
            'messages':[{'role':'system','content':LISTING_SYSTEM},{'role':'user','content':facts},
                {'role':'assistant','content':json.dumps({'title':title,'body':body},ensure_ascii=False)}]})
    return rows


def build():
    return validate_dataset({'version':1,'provenance':{'kind':'assistant-teacher-targeted-lessons',
        'purpose':'Short faithful bilingual rental clauses plus concise factual Chinese advertisements',
        'authorship':'development assistant; not human/native-editor approved',
        'source':'Generic lessons selected after inspection of recent rental source descriptions; no private listing identities',
        'permission':'User explicitly requested assistant teaching of local models',
        'weightsAutomaticallyPromoted':False},
        'splits':{'train':translations(TRAIN,'train')+ads('train',24,0),
                  'validation':translations(VALID,'valid')+ads('valid',6,40)}})


def main():
    paths = [ROOT/'data/learning/teacher-rental-v30.json', ROOT/'data/quality/teacher-rental-heldout-v30.json']
    values = [build(), {'version':1,'authorship':'assistant-authored independent diagnostic questions',
        'nativeSpeakerReviewed':False,'trainingUseAllowed':False,'frozenBeforeRun':True,
        'cases':translations(HELDOUT,'heldout')}]
    result = {}
    for path, value in zip(paths, values):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        result[str(path.relative_to(ROOT))] = {'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    result['counts']={key:len(rows) for key,rows in values[0]['splits'].items()}
    result['heldoutQueries']=len(values[1]['cases'])
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
