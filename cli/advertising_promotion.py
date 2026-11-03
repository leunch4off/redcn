"""Measured, fact-bound acceptance for the actual 4B advertising adapter.

The validation properties never supply gradients. Both model sides answer the
same live canonical requests. A loss decrease, saved labels, or a running
scheduler cannot select weights. This lane changes only advertising settings.
"""
import json
import math
from pathlib import Path
import re
import time

from advertising_learning import validate_training_dataset
from model_promotion import (_json, _file, _under_cache, raw_config, _selection_lock,
                             renderer_artifacts)
from text_model_contract import (content_sha, load_dataset, training_contract,
                                 inference_sampling_parameters)

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = 1
POLICY = {'name':'paired-grounded-advertising-v1','seed':20261103,'variants':2,
          'minimumHoldoutGroups':4,'probeSeconds':55,'maximumAgeSeconds':86400,
          'requiresEveryCandidateProbe':True,'requiresStrictImprovement':True,
          'fallbackAllowed':False,'promptContract':'live-individual-copy',
          'humanEditorialQualityCertified':False}
CHECKERS = ('advertising_promotion.py','advertising_learning.py','individual_copy.py',
            'local_text_model.py','text_model_contract.py','learning_memory.py',
            'learning_provenance.py','text_evaluation_process.py','text_native_evaluation.py',
            'fact_ledger.py','rental_conditions.py','language_service.py','node_runtime.py')


def adapter_identity(path, model, *, verify_base=True):
    folder, model = Path(path), Path(model)
    if folder.is_symlink(): raise ValueError('Каталог адаптера не должен быть ссылкой')
    receipt = _json(folder/'trainreceipt.json',maximum=2_000_000)
    contract = training_contract(_json(model/'config.json',maximum=100_000),'advertising')
    config = _json(folder/'adapter_config.json',maximum=100_000)
    digest = _file(folder/'adapters.safetensors',8_000_000)
    if (receipt.get('status') != 'trained-unaccepted' or receipt.get('trainingLane') != 'advertising'
            or receipt.get('trainingContract') != contract['name'] or receipt.get('baseModel') != contract['baseModel']
            or receipt.get('targetFormat') != contract['targetFormat']
            or receipt.get('trainableParameters') != contract['trainableParameters']
            or receipt.get('layers') != contract['layers'] or receipt.get('lora') != contract['lora']
            or receipt.get('stoppedEarly') is not False or type(receipt.get('stepsCompleted')) is not int
            or not 1 <= receipt['stepsCompleted'] <= 2000
            or receipt['stepsCompleted'] != receipt.get('limits',{}).get('steps')
            or type(receipt.get('changedParameterElements')) is not int or receipt['changedParameterElements'] <= 0
            or receipt.get('adapterSha256') != digest
            or receipt.get('baseConfigSha256') != _file(model/'config.json',100_000)
            or config.get('fine_tune_type') != 'lora' or config.get('num_layers') != contract['layers']
            or config.get('lora_parameters') != contract['lora']):
        raise ValueError('Кандидат объявлений не соответствует завершённому обучению этой базовой модели')
    if verify_base:
        artifacts={item.name:_file(item) for item in sorted(model.glob('*.safetensors'))}
        support={item.name:_file(item,20_000_000) for item in sorted(model.iterdir())
            if item.name.endswith(('.json','.model','.tiktoken','.jinja'))}
        if not artifacts or receipt.get('baseArtifacts')!=artifacts or receipt.get('baseSupportArtifacts')!=support:
            raise ValueError('Адаптер обучен на других весах базовой модели')
    return {'path':str(folder.resolve()),'weightsSha256':digest,
        'receiptSha256':_file(folder/'trainreceipt.json',2_000_000),
        'support':{'adapter_config.json':_file(folder/'adapter_config.json',100_000)}}


def prepare_probes(data):
    """Complete validation properties, each requested twice using live prompts."""
    from individual_copy import build_brief, generation_request
    rows = data['splits']['validation']
    if len({row['groupId'] for row in rows}) < POLICY['minimumHoldoutGroups']:
        raise ValueError('Нужны минимум четыре независимые проверочные квартиры')
    result = []
    for row in rows:
        frozen = row['grounding']
        brief = build_brief(frozen['fields'],frozen['rules'])
        for variant in range(POLICY['variants']):
            seed = POLICY['seed']+len(result)
            result.append({'id':row['id']+'-v'+str(variant+1),'caseId':row['groupId'],
                'task':'individual-listing','brief':brief,
                'request':generation_request(brief,seed=seed)})
    return result


def capture_inputs(directory, candidate, dataset):
    from learning_memory import LearningMemory
    cache = Path(directory).resolve()
    memory = LearningMemory(cache); memory.assert_dataset_consent(dataset)
    candidate = _under_cache(cache,candidate)
    config = raw_config(cache); value = config['value']
    if value.get('enabled') is not True or not value.get('advertisingModelPath'):
        raise ValueError('Нужна включённая отдельная модель объявлений')
    model_path = Path(value['advertisingModelPath'])
    if model_path.is_symlink(): raise ValueError('База модели не должна быть ссылкой')
    model = model_path.resolve()
    contract = training_contract(_json(model/'config.json',maximum=100_000),'advertising')
    base = {'configSha256':_file(model/'config.json',100_000),
        'artifacts':{path.name:_file(path) for path in sorted(model.glob('*.safetensors'))},
        'support':{path.name:_file(path,20_000_000) for path in sorted(model.iterdir())
            if path.name.endswith(('.json','.model','.tiktoken','.jinja'))}}
    if not base['artifacts']: raise ValueError('Веса базовой модели отсутствуют')
    selected = value.get('advertisingAdapterPath') if value.get('useAdvertisingAdapter') is True else None
    current = adapter_identity(selected,model) if selected else None
    target = adapter_identity(candidate,model)
    training = validate_training_dataset(load_dataset(dataset))
    probes = prepare_probes(training)
    dataset_sha = _file(dataset,8_000_000)
    receipt = _json(candidate/'trainreceipt.json',maximum=2_000_000)
    if (receipt.get('datasetSha256') != dataset_sha or receipt.get('baseArtifacts') != base['artifacts']
            or receipt.get('baseSupportArtifacts') != base['support']):
        raise ValueError('Данные или веса базы отличаются от фактически обученного кандидата')
    from learning_provenance import candidate_status
    with memory._db() as db:
        consent = candidate_status(db,target['weightsSha256'])
        lineage = db.execute('SELECT dataset_sha FROM learning_candidates WHERE weights_sha=?',
            (target['weightsSha256'],)).fetchone()
    if not consent['lineageRecorded'] or consent['retrainingRequired'] or not lineage or lineage[0] != dataset_sha:
        raise ValueError('Кандидат не связан с действующим разрешением на учебные примеры')
    return {'lane':'advertising','runtimeConfig':config,'modelPath':str(model),'base':base,
        'trainingContract':contract,'current':current,'candidate':target,
        'datasetPath':str(Path(dataset).resolve()),'datasetSha256':dataset_sha,
        'casesSha256':content_sha(training['splits']['validation']),
        'validationGroups':len({row['groupId'] for row in training['splits']['validation']}),
        'trainingGroups':len({row['groupId'] for row in training['splits']['train']}),
        'independence':{'trainingValidationDisjoint':True,'inputDisjoint':True,'targetsReceivedGradients':False},
        'probeRequestsSha256':content_sha([{'id':row['id'],'factsHash':row['brief']['factsHash'],
            'request':row['request']} for row in probes]),
        'checkers':{name:_file(ROOT/'cli'/name) for name in CHECKERS},
        'rendererArtifacts':renderer_artifacts(),
        'evaluatorSha256':_file(ROOT/'scripts/evaluate-advertising-adapter.py'),'policy':POLICY}


def assess(probe, text):
    from individual_copy import parse_individual_text, validate_individual
    try:
        draft = parse_individual_text(text)
        issues = validate_individual(draft,probe['brief'])
        return {'passed':not issues,'issues':issues,'draft':draft}
    except (ValueError,TypeError) as error:
        return {'passed':False,'issues':[str(error)],'draft':None}


def compare_rows(data, current, candidate, *, prepared=None):
    probes = prepared if prepared is not None else prepare_probes(data)
    expected = [probe['id'] for probe in probes]
    checks, distinct = {}, {}
    for side, rows in (('current',current),('candidate',candidate)):
        if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows) or [row.get('id') for row in rows] != expected:
            return {'accepted':False,'reason':'incomplete-probes','message':'Нужны все реальные ответы обеих моделей на одинаковые проверочные запросы.'}
        scores, groups = [], {}
        for row, probe in zip(rows,probes):
            request = probe['request']; seconds = row.get('seconds')
            sampler = inference_sampling_parameters('mlx-community/Qwen3-4B-Instruct-2507-4bit',request['temperature'])
            measured = (row.get('status') == 'measured' and row.get('backend') == 'mlx'
                and row.get('fallback') is False and row.get('caseId') == probe['caseId']
                and row.get('requestSha256') == content_sha(request)
                and row.get('seed') == request['seed'] and row.get('maxTokens') == request['maxTokens']
                and row.get('sampling') == sampler and row.get('generationTokensMeasured') is True
                and type(row.get('generationTokens')) is int and 0 < row['generationTokens'] < request['maxTokens']
                and row.get('stopReason') in ('eos','stream-ended')
                and isinstance(row.get('text'),str) and 1 <= len(row['text']) <= 20_000
                and type(seconds) in (int,float) and math.isfinite(seconds) and 0 <= seconds <= POLICY['probeSeconds']+1)
            check = assess(probe,row['text']) if measured else {'passed':False,'draft':None}
            scores.append(check['passed'])
            if check['draft']:
                normalized = re.sub(r'[\s\W_]+','',check['draft']['body']).casefold()
                groups.setdefault(probe['caseId'],set()).add(normalized)
        checks[side] = scores
        distinct[side] = sum(len(values)==POLICY['variants'] for values in groups.values())
    before, after = checks['current'], checks['candidate']
    failures = [probe['id'] for probe,passed in zip(probes,after) if not passed]
    regressions = [probe['id'] for probe,was,now in zip(probes,before,after) if was and not now]
    group_count = len({probe['caseId'] for probe in probes})
    improved = sum(after)>sum(before) or distinct['candidate']>distinct['current']
    accepted = not failures and not regressions and distinct['candidate']==group_count and improved
    reason = ('accepted' if accepted else 'candidate-factual-errors' if failures else
        'repeated-variants' if distinct['candidate']<group_count else 'no-measured-improvement')
    messages = {'accepted':'Адаптер объявлений прошёл все проверочные факты и улучшил измеряемый результат.',
        'candidate-factual-errors':'Кандидат пропускает или искажает проверочные факты; прежние веса сохранены.',
        'repeated-variants':'Проверочные варианты повторяются; прежние веса сохранены.',
        'no-measured-improvement':'Измеримого улучшения перед текущей моделью нет; прежние веса сохранены.'}
    return {'accepted':accepted,'reason':reason,'message':messages[reason],'requested':len(probes),
        'currentPassed':sum(before),'candidatePassed':sum(after),'regressions':regressions,
        'candidateFailures':failures,'currentDistinctCases':distinct['current'],
        'candidateDistinctCases':distinct['candidate'],'nativeSpeakerReviewCompleted':False,
        'humanEditorialQualityCertified':False,'lane':'advertising'}


def validate_report(directory, path, *, now=None):
    from text_evaluation_process import inspect_side
    report = _json(path); now = time.time() if now is None else now
    finished, inputs = report.get('finishedAt'), report.get('inputs')
    if (report.get('version') != REPORT_VERSION or report.get('lane') != 'advertising'
            or type(finished) not in (int,float) or not math.isfinite(finished)
            or not 0 <= now-finished <= POLICY['maximumAgeSeconds'] or not isinstance(inputs,dict)):
        raise ValueError('Отчёт объявлений устарел или не соответствует контракту')
    fresh = capture_inputs(directory,inputs.get('candidate',{}).get('path',''),inputs.get('datasetPath',''))
    if fresh != inputs: raise ValueError('Факты, программа, настройки или веса изменились после проверки')
    data = load_dataset(inputs['datasetPath']); prepared = prepare_probes(data)
    expected = [row['id'] for row in prepared]
    if report.get('fullGateEligible') is not True or report.get('inputsStillFresh') is not True:
        return report, {'accepted':False,'reason':'incomplete-probes','message':'Незавершённый native-прогон не разрешает выбор весов.'}
    attempt = Path(path).with_name(Path(path).name+'.attempt')
    for side in ('current','candidate'):
        process = report.get('processAttempts',{}).get(side,{})
        inspected = inspect_side(attempt/side,side,content_sha(inputs),expected,process.get('returnCode'))
        if (not inspected['admitted'] or inspected['terminal'].get('nativeArm64') is not True
                or inspected['rows'] != report.get(side)):
            raise ValueError('Ответы не соответствуют завершённому изолированному native-процессу')
    return report, compare_rows(data,report.get('current'),report.get('candidate'),prepared=prepared)


def record_evaluation(directory, path):
    from learning_scheduler import _folder, _write
    report, decision = validate_report(directory,path)
    receipt = _json(Path(report['inputs']['candidate']['path'])/'trainreceipt.json')
    from model_history import record_evaluation as save_history
    save_history(directory,report,decision,receipt,report_sha=_file(path))
    _write(_folder(directory)/'advertising-model-evaluation.json',{'version':1,'lane':'advertising',
        'at':report['finishedAt'],'decision':decision,'stepsCompleted':receipt['stepsCompleted'],
        'trainedParameters':receipt['trainableParameters'],'candidateSha256':receipt['adapterSha256'],
        'reportSha256':_file(path)})
    return decision


def promote(directory, report_path, *, automatic=False, now=None):
    from learning_scheduler import _folder, _write, load_config, model_resource
    with _selection_lock(directory), model_resource(directory,kind='background'):
        if automatic and not load_config(directory)['automaticPromotion']:
            raise ValueError('Автоматическое принятие новых весов выключено')
        report, decision = validate_report(directory,report_path,now=now)
        if not decision['accepted']: return decision
        before = raw_config(directory)
        after = {**before['value'],'advertisingAdapterPath':report['inputs']['candidate']['path'],
                 'useAdvertisingAdapter':True}
        if before['value']==after: raise ValueError('Этот адаптер объявлений уже выбран')
        journal = {'version':1,'lane':'advertising','state':'prepared','at':time.time(),
            'before':before,'after':after,'previousAdapter':report['inputs']['current'],
            'base':report['inputs']['base'],'modelPath':report['inputs']['modelPath'],
            'reportSha256':_file(report_path),'decision':decision,'automatic':automatic}
        path = _folder(directory)/'advertising-model-promotion.json'; _write(path,journal)
        if raw_config(directory)!=before: raise ValueError('Настройки изменились во время приёмки')
        _write(Path(directory)/'text-ai.json',after)
        journal['state']='accepted'; _write(path,journal)
        return {**decision,'selected':True,'rollbackAvailable':True}


def rollback(directory):
    from learning_scheduler import _folder, _write, model_resource
    with _selection_lock(directory),model_resource(directory,kind='background'):
        path = _folder(directory)/'advertising-model-promotion.json'; journal = _json(path)
        current = raw_config(directory)['value']; keys = ('advertisingAdapterPath','useAdvertisingAdapter')
        if journal.get('state') not in ('accepted','prepared') or any(current.get(key)!=journal.get('after',{}).get(key) for key in keys):
            raise ValueError('Адаптер объявлений уже изменился; откат не перезапишет новый выбор')
        previous = journal.get('previousAdapter')
        if previous and adapter_identity(previous['path'],journal['modelPath'])!=previous:
            raise ValueError('Предыдущий адаптер изменился')
        model = Path(journal['modelPath'])
        support={p.name:_file(p,20_000_000) for p in sorted(model.iterdir())
            if p.name.endswith(('.json','.model','.tiktoken','.jinja'))}
        if (_file(model/'config.json')!=journal['base']['configSha256']
                or {p.name:_file(p) for p in sorted(model.glob('*.safetensors'))}!=journal['base']['artifacts']
                or support!=journal['base'].get('support')):
            raise ValueError('База модели изменилась')
        restored = dict(current)
        for key in keys:
            if key in journal['before']['value']: restored[key]=journal['before']['value'][key]
            else: restored.pop(key,None)
        _write(Path(directory)/'text-ai.json',restored)
        journal.update(state='rolled-back',rolledBackAt=time.time()); _write(path,journal)
        return {'rolledBack':True,'lane':'advertising','message':'Восстановлен предыдущий адаптер объявлений. Профиль перевода сохранён.'}


def public_status(directory):
    try:
        root = Path(directory)/'learning'
        journal = _json(root/'advertising-model-promotion.json',default={})
        latest = _json(root/'advertising-model-evaluation.json',default={})
        measured = latest.get('decision',{})
        current = raw_config(directory)['value']
        selected = journal.get('state') in ('accepted','prepared') and all(current.get(key)==journal.get('after',{}).get(key)
            for key in ('advertisingAdapterPath','useAdvertisingAdapter'))
        return {'lane':'advertising','state':journal.get('state','not-evaluated'),'rollbackAvailable':selected,
            'message':journal.get('decision',{}).get('message','Адаптер объявлений ещё не принят после полного сравнения.'),
            'latestEvaluation':{**{key:measured.get(key) for key in ('reason','message','requested','candidatePassed','currentPassed')},
                'stepsCompleted':latest.get('stepsCompleted'),'recordedAt':latest.get('at')},
            'nativeSpeakerReviewCompleted':False}
    except (ValueError,OSError,TypeError):
        return {'lane':'advertising','state':'unavailable','rollbackAvailable':False,'message':'Квитанция адаптера объявлений требует проверки.'}
