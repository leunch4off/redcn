"""Prepare a small attributed bilingual experiment from curated project language.

The 89171-entry open lexicon remains review-only and is deliberately not turned
into assumed ground truth. The seed covers language, not real apartment claims.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from text_model_contract import file_sha, validate_dataset

SYSTEM = '你是莫斯科租房内容编辑。只使用已确认的事实，保留数字与专有名词；不得补充家具、费用、入住政策、路线或热度。'


def prepare(project):
    source_path = Path(project)/'data'/'chinese-rental-research.json'
    source = json.loads(source_path.read_text())
    eligible = [row for row in source['entries']
        if row.get('origin') == 'editorial' and row.get('review') == 'curated-language-not-a-property-fact'
        and len(row.get('ru', '')) < 65 and len(row.get('zh', '')) < 55
        and row.get('category') not in ('registration', 'fees')]
    # Select a bounded, deterministic spread rather than the first related terms.
    eligible.sort(key=lambda row: hashlib.sha256(row['id'].encode()).hexdigest())
    rows = []
    for item in eligible[:60]:
        group = 'lexical:'+item['ru'].strip().lower()
        for direction, phrase, answer in [('ru-zh', item['ru'], item['zh']), ('zh-ru', item['zh'], item['ru'])]:
            prompt = ('Переведи на упрощённый китайский: ' if direction == 'ru-zh' else 'Переведи на русский: ')+phrase
            rows.append({'id': item['id']+':'+direction, 'groupId': group,
                'source': 'project-editorial:data/chinese-rental-research.json#'+item['id'],
                'status': 'project-authored', 'task': 'translation', 'sourceIds': item.get('sourceIds', []),
                'messages': [{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': answer}]})
    # Twelve deliberately authored fictional facts/outputs, with varied priorities,
    # layout and punctuation. They teach bounded writing, never property inventory.
    examples = [
        ({'location':'莫斯科，大学站','price':75000,'area':40,'confirmed':['独立卧室','书桌','衣柜']},
         '🏠 大学站｜40㎡独立卧室，7.5万卢布\n\n租金75000卢布/月。卧室之外，书桌与衣柜也已确认，学习和收纳都有安排。\n\n#莫斯科租房 #大学站'),
        ({'location':'莫斯科，普罗克希诺','price':95000,'area':52,'confirmed':['两间独立卧室','烤箱','冰箱']},
         '🚇 普罗克希诺·52㎡独立双卧\n\n95000卢布/月｜52㎡\n两间卧室互相独立；厨房确认配有烤箱和冰箱。\n\n#莫斯科租房 #独立双卧'),
        ({'location':'莫斯科，萨拉里耶沃','price':65000,'area':32,'confirmed':['洗衣机','阳台']},
         '萨拉里耶沃｜32㎡，每月6.5万卢布\n\n🧺 洗衣机已确认，另有阳台。\n月租65000卢布，面积32㎡。\n\n#莫斯科租房 #萨拉里耶沃'),
        ({'location':'莫斯科，波塔波沃','price':80000,'area':50,'confirmed':['双人床','餐桌','两把椅子']},
         '50㎡住处·波塔波沃，8万卢布/月\n\n🛏 已确认双人床，餐桌旁有两把椅子。面积50㎡，月租80000卢布。\n\n#莫斯科租房 #波塔波沃'),
        ({'location':'莫斯科，别利亚耶沃','price':70000,'area':38,'confirmed':['沙发','电视']},
         '📍 别利亚耶沃：38㎡，月租7万\n\n沙发和电视是这套已确认的配置。\n38㎡｜70000卢布/月\n\n#莫斯科租房 #别利亚耶沃'),
        ({'location':'莫斯科，索科尔','price':90000,'area':61,'confirmed':['两间独立卧室','衣帽间']},
         '索科尔双卧｜61㎡与独立衣帽间\n\n90000卢布/月。两间卧室独立，另设衣帽间，收纳空间有清晰分区。\n\n#莫斯科租房 #独立双卧'),
        ({'location':'莫斯科，鲁缅采沃','price':60000,'area':35,'confirmed':['微波炉','冰箱']},
         '🍽 鲁缅采沃｜35㎡，6万卢布/月\n\n厨房已确认微波炉和冰箱。\n月租60000卢布，面积35㎡。\n\n#莫斯科租房 #鲁缅采沃'),
        ({'location':'莫斯科，鲍曼','price':85000,'area':44,'confirmed':['书桌','电脑']},
         '鲍曼站·书桌与电脑已配备\n\n44㎡，85000卢布/月。已确认书桌和电脑，相关配置可以一并查看。\n\n#莫斯科租房 #鲍曼'),
        ({'location':'莫斯科，奥尔霍瓦亚','price':72000,'area':42,'confirmed':['洗碗机','烤箱']},
         '42㎡｜奥尔霍瓦亚，月租7.2万卢布\n\n🍳 烤箱与洗碗机已确认。租金72000卢布/月，面积42㎡。\n\n#莫斯科租房 #奥尔霍瓦亚'),
        ({'location':'莫斯科，拉斯卡佐夫卡','price':68000,'area':37,'confirmed':['钢琴','衣柜']},
         '🎹 拉斯卡佐夫卡｜带钢琴的37㎡住处\n\n每月68000卢布。已确认钢琴和衣柜，面积37㎡。\n\n#莫斯科租房 #拉斯卡佐夫卡'),
        ({'location':'莫斯科，新莫斯科','price':78000,'area':45,'confirmed':['封闭式阳台','双人床']},
         '新莫斯科·45㎡｜7.8万卢布/月\n\n双人床与封闭式阳台均已确认。\n45㎡，月租78000卢布。\n\n#莫斯科租房 #新莫斯科'),
        ({'location':'莫斯科，久津诺','price':82000,'area':48,'confirmed':['餐桌','洗衣机']},
         '久津诺｜48㎡，8.2万卢布\n\n🧺 已确认洗衣机，另有餐桌。面积48㎡，租金82000卢布/月。\n\n#莫斯科租房 #久津诺'),
    ]
    for index, (facts, text) in enumerate(examples):
        title, body = text.split('\n\n', 1)
        body = body.rsplit('\n\n#', 1)[0]
        answer = json.dumps({'title':title, 'body':body}, ensure_ascii=False)
        rows.append({'id': f'authored-card-{index+1}', 'groupId': f'fictional-property-{index+1}',
            'source':'project-authored:local-text-training-seed-v1', 'status':'project-authored',
            'task':'listing', 'fictional':True, 'messages':[{'role':'system','content':SYSTEM},
                {'role':'user','content':'根据确认字段写简短标题和说明。只返回JSON，两个键title和body，不要标签。'+json.dumps(facts, ensure_ascii=False)},
                {'role':'assistant','content':answer}]})
    splits = {'train': [], 'validation': []}
    for row in rows:
        split = 'validation' if int(hashlib.sha256(row['groupId'].encode()).hexdigest()[:8], 16)%5 == 0 else 'train'
        splits[split].append(row)
    return validate_dataset({'version':1, 'purpose':'initial supervised bilingual experiment; unaccepted',
        'provenance': {'path':'data/chinese-rental-research.json', 'sha256':file_sha(source_path),
            'selection':'60 bounded editorial terms plus12 authored fictional cards; open review-only lexicon excluded'},
        'splits':splits})


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--output', required=True)
    args=parser.parse_args()
    target=Path(args.output)
    if target.exists():
        raise ValueError('Use a new dataset filename to preserve training provenance')
    value=prepare(args.project)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'sha256':file_sha(target), 'examples':{key:len(rows) for key,rows in value['splits'].items()}}, ensure_ascii=False))
