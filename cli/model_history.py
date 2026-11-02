"""Bounded, private candidate history. Live selection always comes from settings."""
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

_SHA = re.compile(r'[a-f0-9]{64}')
MAX_HISTORY = 200
_REASONS = {'accepted','incomplete-probes','candidate-factual-errors','no-strict-improvement',
            'regressions','insufficient-diversity','not-evaluated','quality-blocked',
            'repeated-variants','no-measured-improvement'}


def _read(path, maximum=2_000_000):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError('Квитанция модели недоступна или слишком велика')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Неверная квитанция модели')
    return value


def _sha(path, maximum=10_000_000):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError('Артефакт модели недоступен')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value, *, integer=False):
    allowed = (int,) if integer else (int, float)
    return value if type(value) in allowed and 0 <= value <= 10**15 and math.isfinite(value) else None


def _hash_value(value):
    return value if isinstance(value,str) and _SHA.fullmatch(value) else None


def _public_record(value):
    if not isinstance(value,dict):
        return None
    decision = value.get('decision') if isinstance(value.get('decision'),dict) else {}
    examples = value.get('examples') if isinstance(value.get('examples'),dict) else {}
    return {'version':1,'scope':'diagnostic-only' if value.get('scope')=='diagnostic-only' else 'production-gate',
            'origin':'imported-historical' if value.get('origin')=='imported-historical' else 'local-evaluator',
            'freshProductionAcceptanceRequired':True, **{key:_hash_value(value.get(key)) for key in
            ('weightsSha256','reportSha256','datasetSha256','casesSha256','checkersSha256')},
            'recordedAt':_number(value.get('recordedAt')),
            'decision':{'accepted':decision.get('accepted') is True,
                'reason':decision.get('reason') if decision.get('reason') in _REASONS else 'not-evaluated',
                **{key:_number(decision.get(key),integer=True) for key in ('requested','currentPassed','candidatePassed')},
                'regressionCount':len(decision['regressions']) if isinstance(decision.get('regressions'),list) else _number(decision.get('regressionCount'),integer=True),
                'candidateFailureCount':len(decision['candidateFailures']) if isinstance(decision.get('candidateFailures'),list) else _number(decision.get('candidateFailureCount'),integer=True)},
            'stepsCompleted':_number(value.get('stepsCompleted'),integer=True),
            'examples':{key:_number(examples.get(key),integer=True) for key in ('train','validation')},
            'seconds':_number(value.get('seconds')),'peakMlxBytes':_number(value.get('peakMlxBytes'),integer=True),
            'historicalResult':True,'freshValidationRequiredForSelection':True}


def _connection(directory):
    from learning_memory import LearningMemory
    memory = LearningMemory(directory)
    return memory._db()


def record_evaluation(directory, report, decision, receipt, *, report_sha, scope='production-gate'):
    """Called only after the evaluator validates a report; never accepts weights."""
    inputs = report.get('inputs', {})
    if scope not in {'production-gate','diagnostic-only'}:raise ValueError('Неизвестный вид проверки модели')
    digest = inputs.get('candidate', {}).get('weightsSha256')
    if not isinstance(digest, str) or not _SHA.fullmatch(digest) or not _SHA.fullmatch(report_sha):
        raise ValueError('История требует контрольных сумм модели и отчёта')
    if receipt.get('adapterSha256') != digest:
        raise ValueError('Квитанция относится к другим весам')
    decision_fields = {key:decision.get(key) for key in ('accepted','reason','requested','currentPassed',
                       'candidatePassed','regressions','candidateFailures','currentDistinctCases','candidateDistinctCases')}
    # No prompts, answers, paths, dataset row IDs, credentials, or free-form
    # external message enter this public summary.
    payload = {'version':1,'scope':scope,'freshProductionAcceptanceRequired':True,
               'weightsSha256':digest,'recordedAt':report.get('finishedAt'),
               'reportSha256':report_sha, 'datasetSha256':inputs.get('datasetSha256'),
               'casesSha256':inputs.get('casesSha256'), 'decision':decision_fields,
               'stepsCompleted':_number(receipt.get('stepsCompleted'),integer=True),
               'examples':{key:_number(receipt.get('examples',{}).get(key),integer=True) for key in ('train','validation')},
               'seconds':_number(receipt.get('elapsedSeconds')),
               'peakMlxBytes':_number(receipt.get('peakMemoryBytes'),integer=True),
               'checkersSha256':hashlib.sha256(json.dumps(inputs.get('checkers',{}),sort_keys=True).encode()).hexdigest(),
               'historicalResult':True,'freshValidationRequiredForSelection':True}
    with _connection(directory) as db:
        db.execute('INSERT OR IGNORE INTO learning_model_history(report_sha,weights_sha,payload,recorded) VALUES (?,?,?,?)',
                   (report_sha,digest,json.dumps(payload,sort_keys=True),time.time()))
        db.execute('DELETE FROM learning_model_history WHERE sequence NOT IN (SELECT sequence FROM learning_model_history ORDER BY sequence DESC LIMIT ?)', (MAX_HISTORY,))
    return payload


def public_history(directory, limit=25):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Предел истории моделей: 1–100')
    root = Path(directory).resolve()
    from local_text_model import settings
    config = settings(root)
    selected = Path(config['adapterPath']).resolve() if config.get('adapterPath') else None
    folders = []
    for parent, child in ((root/'tools/text-ai/runs',None),(root/'learning/training-runs','candidate'),(root/'learning/imported-models',None)):
        if parent.is_symlink() or not parent.is_dir():
            continue
        entries = list(parent.iterdir())
        if len(entries)>1000:
            raise ValueError('История содержит слишком много каталогов кандидатов')
        folders.extend((path/child if child else path) for path in entries if path.is_dir() and not path.is_symlink())
    if selected and selected.is_relative_to(root) and selected not in folders:
        folders.append(selected)
    records = {}
    db_path = root/'learning/memory.sqlite3'
    if db_path.is_file() and not db_path.is_symlink():
        with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_model_history'").fetchone():
                for weights,report_sha,payload in db.execute('SELECT weights_sha,report_sha,payload FROM learning_model_history ORDER BY sequence DESC LIMIT ?', (MAX_HISTORY,)):
                    if weights not in records:
                        try:
                            record=_public_record(json.loads(payload))
                            if record and record['weightsSha256']==weights and record['reportSha256']==report_sha:
                                records[weights]=record
                        except (ValueError,TypeError):continue
    candidates = []
    for folder in folders:
        restored=(folder/'historical-trainreceipt.json').is_file()
        receipt_path=folder/('historical-trainreceipt.json' if restored else 'trainreceipt.json')
        if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(root):
            continue
        from training_job_state import activity
        current_activity=activity(folder)
        if not receipt_path.is_file() and not current_activity['active']:continue
        if restored and (folder/'model.onnx').is_file() and not (folder/'adapters.safetensors').exists():continue
        identifier = hashlib.sha256(str(folder.relative_to(root)).encode()).hexdigest()[:20]
        base = {'id':identifier,'selected':folder.resolve()==selected,'historicalEvaluation':None,'activity':current_activity}
        try:
            receipt = _read(receipt_path)
            examples = receipt.get('examples') if isinstance(receipt.get('examples'),dict) else {}
            digest = _sha(folder/'adapters.safetensors')
            bound = receipt.get('adapterSha256') == digest
            state = ('training' if current_activity['active'] else 'invalid-receipt' if not bound else 'restored-unaccepted' if restored else 'paused' if receipt.get('stoppedEarly') is True
                     else 'trained-unaccepted' if receipt.get('status')=='trained-unaccepted' else 'unfinished')
            row = {**base,'state':state,'weightsSha256':digest,'receiptMatchesWeights':bound,
                   'receiptModifiedAt':receipt_path.stat().st_mtime,
                   'datasetSha256':receipt.get('datasetSha256') if isinstance(receipt.get('datasetSha256'),str) and _SHA.fullmatch(receipt['datasetSha256']) else None,
                   'stepsCompleted':_number(receipt.get('stepsCompleted'),integer=True),
                   'trainableParameters':_number(receipt.get('trainableParameters'),integer=True),
                   'examples':{key:_number(examples.get(key),integer=True) for key in ('train','validation')},
                   'seconds':_number(receipt.get('elapsedSeconds')),'peakMlxBytes':_number(receipt.get('peakMemoryBytes'),integer=True),
                   'validationLoss':_number(receipt.get('finalValidationLoss')),
                   'historicalEvaluation':records.get(digest) if bound else None,
                   'freshValidationRequiredForSelection':True}
            if db_path.is_file() and not db_path.is_symlink():
                with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_candidates'").fetchone():
                        from learning_provenance import candidate_status
                        row['consent'] = candidate_status(db,digest)
            candidates.append(row)
        except (OSError,ValueError,TypeError,sqlite3.Error):
            candidates.append({**base,'state':'training' if current_activity['active'] else 'invalid-receipt','receiptMatchesWeights':False})
    candidates.sort(key=lambda row:(row['selected'],row.get('receiptModifiedAt',0)),reverse=True)
    active = any(row['activity']['active'] for row in candidates)
    return {'version':1,'candidates':candidates[:limit],'total':len(candidates),
            'selectedAdapterPresent':selected is not None,
            'activeTraining':active, 'activeStateSource':'candidate-os-lease',
            'note':'Выбранные веса определяются текущими настройками. Каждая квитанция и проверка относится только к указанной версии; принятие требует новой проверки всех артефактов.'}
