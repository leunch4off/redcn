"""Compare fixed public probes before/after a local adapter; no promotion.

These tiny phrase/number checks are smoke checks, not Chinese editorial review
or a broad measure of translation accuracy. No cache/private listings are read.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from text_model_contract import expected_parameters, file_sha

PROBES = [
    {'id':'ru-zh-furniture', 'messages':[
        {'role':'system','content':'Translate into Simplified Chinese. Return only the translation. Preserve all facts.'},
        {'role':'user','content':'В квартире есть холодильник, диван и двуспальная кровать.'}],
        'requiredAny':[['冰箱'],['沙发'],['双人床','双人大床']], 'numbers':[]},
    {'id':'zh-ru-figures', 'messages':[
        {'role':'system','content':'Переведи на русский. Только перевод, сохрани все числа и факты.'},
        {'role':'user','content':'月租74000卢布，面积39平方米，位于8楼。'}],
        'requiredAny':[['74000'],['39'],['8']], 'numbers':['74000','39','8']},
    {'id':'grounded-json', 'messages':[
        {'role':'system','content':'你是租房编辑。只使用确认事实，不加设备、费用或许可。只返回JSON，两个键title和body。'},
        {'role':'user','content':'地点：莫斯科；月租：74000卢布；面积：39㎡；确认家具：沙发、书桌。写简短标题和说明，保留74000和39两个数字。'}],
        'requiredAny':[['莫斯科'],['沙发'],['书桌']], 'numbers':['74000','39'], 'json':True}
]


def check(probe, text):
    missing = [items for items in probe['requiredAny'] if not any(item.casefold() in text.casefold() for item in items)]
    numbers = set(re.findall(r'\d+(?:[.,]\d+)?', text))
    expected = set(probe['numbers'])
    result = {'requiredTermsPresent':not missing, 'missingTerms':missing,
        'numbersPreserved':not expected or numbers == expected}
    if probe.get('json'):
        try:
            value=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip()))
            result['jsonShapeCorrect'] = isinstance(value,dict) and set(value)=={'title','body'} and all(isinstance(item,str) for item in value.values())
        except ValueError:
            result['jsonShapeCorrect']=False
        result['unsupportedCommonClaims'] = [term for term in ('冰箱','空调','洗衣机','免费','可养宠','落地签','分钟') if term in text]
    return result


def run(args):
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    from mlx_lm.tuner.utils import load_adapters
    model_path=Path(args.model)
    expected_parameters(json.loads((model_path/'config.json').read_text()))
    mx.set_memory_limit(2_500_000_000); mx.set_cache_limit(64_000_000)
    started=time.monotonic()
    model,tokenizer=load(str(model_path))
    phases={}
    for phase in ('baseline','adapter'):
        if phase=='adapter': model=load_adapters(model,str(Path(args.adapter)))
        model.eval(); rows=[]
        for probe in PROBES:
            if time.monotonic()-started >= args.max_seconds: break
            prompt=tokenizer.apply_chat_template(probe['messages'],tokenize=False,add_generation_prompt=True)
            begin=time.monotonic(); pieces=[]; count=0; completed=False
            stream=stream_generate(model,tokenizer,prompt=prompt,max_tokens=args.max_tokens,sampler=make_sampler(temp=0))
            for chunk in stream:
                pieces.append(chunk.text); count+=1
                if time.monotonic()-started >= args.max_seconds: break
            else:
                completed=True
            stream.close()
            text=''.join(pieces)
            rows.append({'id':probe['id'],'text':text,'seconds':time.monotonic()-begin,
                'generatedChunks':count,'completed':completed, 'checks':check(probe,text)})
        phases[phase]=rows
    result={'version':1,'status':'smoke-only-unaccepted','baseConfigSha256':file_sha(model_path/'config.json'),
        'adapterSha256':file_sha(Path(args.adapter)/'adapters.safetensors'),
        'elapsedSeconds':time.monotonic()-started, 'peakMemoryBytes':mx.get_peak_memory(),
        'phases':phases, 'automaticPromotion':False,
        'limitations':['Three short probes cannot establish translation quality or factual reliability.','Phrase matching and numeric preservation are partial checks only.']}
    target=Path(args.output)
    if target.exists(): raise ValueError('Preserve previous measurements; use a new output filename')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:result[key] for key in ('status','elapsedSeconds','peakMemoryBytes','phases')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True); parser.add_argument('--adapter',required=True)
    parser.add_argument('--output',required=True); parser.add_argument('--max-seconds',type=int,default=120)
    parser.add_argument('--max-tokens',type=int,default=160)
    args=parser.parse_args()
    if not 30<=args.max_seconds<=300 or not 32<=args.max_tokens<=256: parser.error('Use bounded evaluation limits')
    run(args)
