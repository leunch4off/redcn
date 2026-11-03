"""Portable model identities/weights; restoration never grants model approval."""
import hashlib
import json
import math
from pathlib import Path
import platform
import re

SHA=re.compile(r'[a-f0-9]{64}')
MAX_MODELS=60
MAX_WEIGHTS=160*1024**2
# Version-1 archives first shipped with these scheduler fields. New optional
# limits may be absent in that historical format, but unknown or incomplete
# original configurations must not be silently accepted.
LEGACY_SCHEDULER_FIELDS={'enabled','idleSeconds','checkSeconds','minNewExamples',
    'maxSteps','maxSeconds','maxSequenceLength','automaticPromotion','evaluationSeconds'}


def _scheduler(value):
    from learning_scheduler import DEFAULTS
    if (not isinstance(value,dict) or not LEGACY_SCHEDULER_FIELDS<=set(value)
            or not set(value)<=set(DEFAULTS)):
        raise ValueError('Неверные параметры обучения')
    return {**DEFAULTS,**value}


def _digest(path,limit):
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=limit:
        raise ValueError('Неверный переносимый файл модели')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
    return digest.hexdigest()


def _json(path,limit=2_000_000):
    if not path.exists():return None
    _digest(path,limit)
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise ValueError('Неверная квитанция модели')
    return value


def _receipt(value):
    """Project a receipt onto a bounded schema, at every nesting level.

    Export drops nonportable details. Import requires the original to equal this
    projection, so added fields, arbitrary strings and invalid scalar types are
    rejected. Historical measurements never authorize execution or selection.
    """
    if not isinstance(value,dict):return {}
    from text_model_contract import INFERENCE_MODELS
    from trained_photo_detector import CLASS_IDS
    def integer(item,low=0,high=2**53-1):
        return type(item) is int and low<=item<=high
    def number(item,low=0,high=2**53-1):
        return type(item) in (int,float) and low<=item<=high and math.isfinite(item)
    def choice(item,options):return isinstance(item,str) and item in options
    def digest(item):return isinstance(item,str) and SHA.fullmatch(item) is not None
    def project(source,rules):
        return {key:source[key] for key,check in rules.items() if key in source and check(source[key])}
    def identifiers(item,allowed):
        return (isinstance(item,list) and 1<=len(item)<=min(100,len(allowed))
                and all(choice(part,allowed) for part in item) and len(set(item))==len(item))
    def shape(item):
        return (isinstance(item,list) and 1<=len(item)<=8
                and all(integer(part,1,1_000_000) for part in item) and math.prod(item)<=1_000_000_000)
    def version(item):
        return (isinstance(item,str) and len(item)<=64 and re.fullmatch(
            r'(?:\d+!)?\d+(?:\.\d+){0,5}(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?',item,re.I|re.ASCII) is not None)
    rules={key:integer for key in ('trainableParameters','adapterTensorCount','layers','seed',
        'stepsCompleted','changedParameterElements','peakMemoryBytes','completedSteps',
        'weightsBytes','trainImages','validationImages')}
    rules.update({key:number for key in ('baselineValidationLoss','finalValidationLoss','elapsedSeconds','seconds')})
    rules.update({key:digest for key in ('baseConfigSha256','datasetSha256','datasetContentSha256',
        'initialSha256','adapterSha256','manifestSha256','pretrainedSha256','weightsSha256')})
    rules.update(version=lambda item:integer(item,1,1000),
        status=lambda item:choice(item,{'training','running','trained-unaccepted','paused','cancelled','failed'}),
        baseModel=lambda item:choice(item,set(INFERENCE_MODELS.values())),
        sampling=lambda item:choice(item,{'random','shuffled'}),
        learningRate=lambda item:number(item,0,1),stoppedEarly=lambda item:type(item) is bool)
    result=project(value,rules)
    nested={
        'examples':{key:integer for key in ('train','validation','test','calibration')},
        'limits':{key:integer for key in ('seconds','steps','maxSequenceLength','memoryBytes','batchSize')},
        'lora':{'rank':lambda item:integer(item,1,1024),'scale':lambda item:number(item,0,1_000_000),
            'dropout':lambda item:number(item,0,1),'keys':lambda item:identifiers(item,
                {'self_attn.q_proj','self_attn.k_proj','self_attn.v_proj','self_attn.o_proj',
                 'mlp.gate_proj','mlp.up_proj','mlp.down_proj'})},
        'onnx':{'sha256':digest,'path':lambda item:item=='model.onnx' and isinstance(item,str),
            'bytes':lambda item:integer(item,1,10_000_000),'inputShape':shape,'outputShape':shape,
            'outputEncoding':lambda item:choice(item,{'raw-yolox'}),
            'classIds':lambda item:identifiers(item,set(CLASS_IDS)),
            'status':lambda item:choice(item,{'exported','complete','skipped','failed'})}}
    for key,schema in nested.items():
        if isinstance(value.get(key),dict):result[key]=project(value[key],schema)
    if isinstance(value.get('baseArtifacts'),dict):
        # These are the fixed model shard basenames produced by the text
        # trainer, never caller-supplied paths or descriptive labels.
        result['baseArtifacts']={name:item for name,item in value['baseArtifacts'].items()
            if isinstance(name,str) and re.fullmatch(r'model(?:-\d{5}-of-\d{5})?\.safetensors',name) and digest(item)}
        if len(result['baseArtifacts'])>50:result.pop('baseArtifacts')
    if isinstance(value.get('packages'),dict):
        names={'mlx','mlx-lm','numpy','Pillow','onnx','onnxruntime','torch','torchvision',
               'safetensors','transformers','tokenizers','huggingface-hub','packaging','sentencepiece'}
        result['packages']={name:item for name,item in value['packages'].items() if name in names and version(item)}
    return result


def snapshot(directory):
    root=Path(directory).resolve()
    from local_text_model import settings
    from learning_scheduler import DEFAULTS,load_config
    from photo_ai_settings import load_config as photo_config
    from text_model_contract import inference_model
    config=settings(root)
    scheduler=load_config(root) if (root/'learning/scheduler.json').is_file() else dict(DEFAULTS)
    photo=photo_config(root)
    package={'version':1,'scheduler':scheduler,
        'preferences':{'textEnabled':config['enabled'],'advertisingEnabled':config['advertisingEnabled'],
            'photoEnabled':photo['enabled'],'photoModel':photo['model'],'photoAutoApply':photo['autoApply']},
        'base':None,'models':[], 'restorePolicy':'disabled-until-native-preflight-and-new-acceptance'}
    base=Path(config['modelPath']).resolve()
    if base.is_relative_to(root) and base.is_dir() and not Path(config['modelPath']).is_symlink():
        source=_json(base/'config.json')
        identity=inference_model(source) if source else None
        if identity:
            names=['config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','generation_config.json']
            names+=sorted(path.name for path in base.glob('*.safetensors'))
            artifact_hashes={name:_digest(base/name,2_000_000_000) for name in names if (base/name).exists()}
            package['base']={'repository':identity,'files':artifact_hashes,
                'identityMethod':'exact-content-sha256','downloadAndVerifyRequired':True}
    selected=Path(config['adapterPath']).resolve() if config.get('adapterPath') else None
    folders=[]
    for parent,child in ((root/'tools/text-ai/runs',None),(root/'learning/training-runs','candidate')):
        if parent.is_symlink() or not parent.is_dir():continue
        entries=list(parent.iterdir())
        if len(entries)>1000:raise ValueError('Слишком много каталогов моделей для копии')
        folders.extend(path/child if child else path for path in entries if path.is_dir() and not path.is_symlink())
    if selected and selected.is_relative_to(root) and selected not in folders:folders.append(selected)
    from trained_photo_detector import ARTIFACT
    photo_folder=root/ARTIFACT
    if photo_folder.is_dir():folders.append(photo_folder)
    blobs={}
    for folder in folders:
        if not folder.resolve().is_relative_to(root):raise ValueError('Версия модели находится вне переносимого хранилища')
        if folder.is_symlink() or not (folder/'trainreceipt.json').is_file():continue
        value=_receipt(_json(folder/'trainreceipt.json'))
        kind='photo' if folder==photo_folder else 'text'
        name='model.onnx' if kind=='photo' else 'adapters.safetensors'
        path=folder/name
        if not path.exists():continue
        digest=_digest(path,10_000_000)
        expected=value.get('onnx',{}).get('sha256') if kind=='photo' else value.get('adapterSha256')
        if expected!=digest:
            # An interrupted checkpoint remains private diagnostic material;
            # it cannot become a transferable finished model.
            continue
        duplicate=next((row for row in package['models'] if row['sha256']==digest),None)
        if duplicate is not None:
            duplicate['selectedAtExport']=duplicate['selectedAtExport'] or folder.resolve()==selected
            continue
        content=path.read_bytes()
        key='learning/artifacts/'+digest
        blobs[key]=content
        package['models'].append({'kind':kind,'sha256':digest,'blob':key,
            'selectedAtExport':folder.resolve()==selected if kind=='text' else photo['model']=='redcn-furniture-v1',
            'receipt':_receipt(value)})
        if len(package['models'])>MAX_MODELS or sum(map(len,blobs.values()))>MAX_WEIGHTS:
            raise ValueError('Версии моделей превышают 60 файлов или 160 МБ; сделайте отдельную копию истории моделей')
    return package,blobs


def validate(package,files):
    if not isinstance(package,dict) or set(package)!={'version','scheduler','preferences','base','models','restorePolicy'} or package['version']!=1 or package['restorePolicy']!='disabled-until-native-preflight-and-new-acceptance':
        raise ValueError('Неверный комплект учебной памяти')
    from learning_scheduler import save_config
    import tempfile
    with tempfile.TemporaryDirectory() as temp:save_config(temp,_scheduler(package['scheduler']))
    prefs=package['preferences']
    if not isinstance(prefs,dict) or set(prefs)!={'textEnabled','advertisingEnabled','photoEnabled','photoModel','photoAutoApply'} or any(type(prefs[key]) is not bool for key in prefs if key!='photoModel'):
        raise ValueError('Неверные предпочтения ИИ')
    from photo_ai_settings import validate_config,DEFAULTS as PHOTO_DEFAULTS
    validate_config({**PHOTO_DEFAULTS,'model':prefs['photoModel']})
    base=package['base']
    if base is not None:
        from text_model_contract import INFERENCE_MODELS
        if not isinstance(base,dict) or set(base)!={'repository','files','identityMethod','downloadAndVerifyRequired'} or base['repository'] not in set(INFERENCE_MODELS.values()) or base['identityMethod']!='exact-content-sha256' or base['downloadAndVerifyRequired'] is not True or not isinstance(base['files'],dict) or not 1<=len(base['files'])<=50:
            raise ValueError('Неверный источник базовой модели')
        for name,digest in base['files'].items():
            if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+\.(?:json|safetensors)',name) or not isinstance(digest,str) or not SHA.fullmatch(digest):raise ValueError('Неверная идентичность базовой модели')
    models=package['models']
    if not isinstance(models,list) or len(models)>MAX_MODELS:raise ValueError('Неверный список моделей')
    seen=set();total=0
    for row in models:
        if not isinstance(row,dict) or set(row)!={'kind','sha256','blob','selectedAtExport','receipt'} or row['kind'] not in ('text','photo') or type(row['selectedAtExport']) is not bool or not isinstance(row['sha256'],str) or not SHA.fullmatch(row['sha256']) or row['sha256'] in seen or row['blob']!='learning/artifacts/'+row['sha256']:
            raise ValueError('Неверная переносимая версия модели')
        seen.add(row['sha256'])
        data=files.get(row['blob'])
        if not isinstance(data,bytes) or not 0<len(data)<=10_000_000 or hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ValueError('Веса модели не совпадают с контрольной суммой')
        value=row['receipt']
        if not isinstance(value,dict) or _receipt(value)!=value:raise ValueError('Квитанция содержит непереносимые поля')
        expected=value.get('onnx',{}).get('sha256') if row['kind']=='photo' and isinstance(value.get('onnx'),dict) else value.get('adapterSha256') if row['kind']=='text' else None
        if expected!=row['sha256']:raise ValueError('Квитанция относится к другим весам')
        total+=len(data)
    if total>MAX_WEIGHTS:raise ValueError('Переносимые веса превышают 160 МБ')
    if {name for name in files if name.startswith('learning/artifacts/')}!={row['blob'] for row in models}:
        raise ValueError('Неописанные файлы моделей')


def restore(directory,package,files):
    validate(package,files)
    root=Path(directory)
    from learning_scheduler import _write,save_config
    save_config(root,{**_scheduler(package['scheduler']),'enabled':False,'automaticPromotion':False})
    for row in package['models']:
        folder=root/'learning/imported-models'/row['sha256']
        folder.mkdir(mode=0o700,parents=True)
        name='adapters.safetensors' if row['kind']=='text' else 'model.onnx'
        (folder/name).write_bytes(files[row['blob']]);(folder/name).chmod(0o600)
        _write(folder/'historical-trainreceipt.json',row['receipt'])
    _write(root/'learning/restored-ai.json',package)
    return {'versions':len(package['models']),'automaticPromotion':False,'trainingEnabled':False,
            'nativePreflightRequired':True,'freshAcceptanceRequired':True,'baseIncluded':False}


def preflight(directory,base_model=None):
    """No executable or path from an archive is ever executed or resolved here."""
    package=_json(Path(directory)/'learning/restored-ai.json')
    if package is None:raise ValueError('Нет восстановленного комплекта ИИ')
    matches={}
    base=package.get('base')
    if base_model and base:
        folder=Path(base_model)
        if folder.is_symlink():raise ValueError('Модель не должна быть ссылкой')
        for name,digest in base['files'].items():
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+\.(?:json|safetensors)',name):raise ValueError('Неверный файл модели')
            try:matches[name]=_digest(folder/name,2_000_000_000)==digest
            except (OSError,ValueError):matches[name]=False
    native=platform.system()=='Darwin' and platform.machine()=='arm64'
    return {'version':1,'nativeAppleSilicon':native,'baseContentMatches':bool(matches) and all(matches.values()),
            'baseFiles':matches,'runtimeExecutionChecked':False,'freshAcceptanceRequired':True,
            'automaticPromotion':False,'readyToActivate':False,
            'next':'Проверить локальную среду MLX/ONNX и повторить независимую приёмку до включения модели.'}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--base-model')
    args=parser.parse_args()
    print(json.dumps(preflight(args.cache,args.base_model),ensure_ascii=False))
