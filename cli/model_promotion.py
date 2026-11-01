"""Fresh paired heldout acceptance and reversible selection of local adapters.

Training receipts are immutable historical records. Loss alone never accepts
an adapter: this gate rechecks the complete measured text against independent
facts, compares the current model, and binds all artifacts and runtime settings.
The checks do not certify native-speaker editorial quality.
"""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import time

from rental_heldout_quality import check_training_disjoint, load_cases, variation_summary
from advertising_evaluation import active_rules, assess, basis, prepare_probes
from text_model_contract import EXPECTED_TRAINABLE, content_sha, file_sha, load_dataset

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT/'data/quality/rental-heldout-v26.json'
CHECKERS = ('model_promotion.py', 'rental_heldout_quality.py', 'text_quality.py',
            'local_content.py', 'local_text_model.py', 'text_model_contract.py',
            'advertising_evaluation.py', 'advertising_styles.py', 'rental_conditions.py',
            'language_service.py', 'phone_relay.py', 'fact_ledger.py', 'node_runtime.py', 'nmt_worker.py')
REPORT_VERSION = 2
POLICY = {'name': 'paired-independent-advertising-v2', 'seed': 20261102,
          'listingMaxTokens': 768, 'translationMaxTokens': 512,
          'listingTemperature': .75, 'translationTemperature': 0,
          'probeSeconds': 55, 'maximumAgeSeconds': 86400,
          'requiresEveryCandidateProbe': True, 'requiresStrictImprovement': True,
          'requiresThreeDistinctVariants': True, 'fallbackAllowed': False,
          'listingSystem': 'production-ADVERTISING_SYSTEM', 'listingBodyHanLimit': 280,
          'independentGenerationFactsRetained': True}


def renderer_artifacts():
    """Bind the renderer, its imported modules and repository dictionaries."""
    paths = [ROOT/'cli/language.mjs', *sorted((ROOT/'web').glob('*.mjs')),
             *sorted((ROOT/'data').rglob('*.json'))]
    return {str(path.relative_to(ROOT)): _file(path) for path in paths}


def _json(path, *, maximum=8_000_000, default=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Файл приёмки не должен быть ссылкой')
    if not path.exists():
        if default is not None:
            return default
        raise ValueError('Файл приёмки отсутствует')
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('Неверный размер файла приёмки')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Ожидается объект приёмки')
    return value


def _file(path, maximum=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or maximum and path.stat().st_size > maximum:
        raise ValueError('Артефакт отсутствует, слишком велик или является ссылкой')
    return file_sha(path)


def _under_cache(directory, adapter):
    cache, adapter = Path(directory).resolve(), Path(adapter)
    if adapter.is_symlink():
        raise ValueError('Каталог адаптера не должен быть ссылкой')
    resolved = adapter.resolve()
    allowed = (cache/'tools/text-ai/runs', cache/'learning/training-runs')
    if not any(resolved.is_relative_to(folder) for folder in allowed):
        raise ValueError('Кандидат должен находиться в частном каталоге учебных прогонов')
    return resolved


def raw_config(directory):
    path = Path(directory)/'text-ai.json'
    return {'exists': path.exists(), 'value': _json(path, maximum=16_000, default={})}


def adapter_identity(path, model):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Каталог адаптера не должен быть ссылкой')
    receipt = _json(path/'trainreceipt.json', maximum=2_000_000)
    digest = _file(path/'adapters.safetensors', 8_000_000)
    if (receipt.get('status') != 'trained-unaccepted'
            or receipt.get('trainableParameters') != EXPECTED_TRAINABLE
            or type(receipt.get('stepsCompleted')) is not int or receipt['stepsCompleted'] <= 0
            or not 0 < (path/'adapters.safetensors').stat().st_size < 8_000_000
            or receipt.get('adapterSha256') != digest
            or receipt.get('baseConfigSha256') != _file(Path(model)/'config.json', 100_000)):
        raise ValueError('Адаптер не соответствует неизменяемой квитанции обучения или базовой модели')
    return {'path': str(path.resolve()), 'weightsSha256': digest,
            'receiptSha256': _file(path/'trainreceipt.json', 2_000_000)}


def _adapter_support(path):
    return {'adapter_config.json': _file(Path(path)/'adapter_config.json', 2_000_000)}


def capture_inputs(directory, candidate, dataset, cases=CASES):
    """Create a complete before/after fingerprint without importing MLX."""
    cache = Path(directory).resolve()
    candidate = _under_cache(cache, candidate)
    config = raw_config(cache)
    value = config['value']
    model = Path(value.get('modelPath', cache/'tools/text-ai/model')).resolve()
    if value.get('enabled') is not True or model.is_symlink() or not model.is_dir():
        raise ValueError('Для сравнения нужна включённая локальная текстовая модель')
    base = {'configSha256': _file(model/'config.json', 100_000),
            'artifacts': {p.name: _file(p) for p in sorted(model.glob('*.safetensors'))}}
    if not base['artifacts']:
        raise ValueError('Не найдены веса базовой модели')
    # Tokenizer and adapter configuration can change model behaviour too.
    base['support'] = {p.name: _file(p, 20_000_000) for p in sorted(model.iterdir())
                       if p.name.endswith(('.json', '.model', '.tiktoken'))}
    selected = value.get('adapterPath') if value.get('useCandidateAdapter') is True else None
    current = adapter_identity(selected, model) if selected else None
    target = adapter_identity(candidate, model)
    target['support'] = _adapter_support(candidate)
    if current:
        current['support'] = _adapter_support(selected)
    training = load_dataset(dataset)
    heldout = load_cases(cases)
    independence = check_training_disjoint(heldout, training)
    heldout_ids = {row['id'] for row in heldout['cases']}
    if any(row['id'] in heldout_ids or row['groupId'] in heldout_ids
           for rows in training['splits'].values() for row in rows):
        raise ValueError('Учебные группы пересекаются с независимым проверочным корпусом')
    training_sha = _file(dataset, 8_000_000)
    receipt = _json(candidate/'trainreceipt.json', maximum=2_000_000)
    if receipt.get('datasetSha256') != training_sha or receipt.get('baseArtifacts') != base['artifacts']:
        raise ValueError('Данные обучения или веса базы не совпадают с квитанцией кандидата')
    if receipt.get('stoppedEarly') is True:
        raise ValueError('Прерванное обучение сначала нужно завершить')
    rules = active_rules(directory)
    advertising_basis = basis(prepare_probes(heldout, rules=rules))
    return {'runtimeConfig': config, 'modelPath': str(model), 'base': base,
            'current': current, 'candidate': target,
            'datasetPath': str(Path(dataset).resolve()), 'datasetSha256': training_sha,
            'casesPath': str(Path(cases).resolve()), 'casesSha256': _file(cases, 200_000),
            'checkers': {name: _file(ROOT/'cli'/name) for name in CHECKERS},
            'rendererArtifacts': renderer_artifacts(),
            'rendererRulesSha256': content_sha(rules),
            'advertisingBasisSha256': content_sha(advertising_basis),
            'evaluatorSha256': _file(ROOT/'scripts/evaluate-text-candidate.py'),
            'policy': POLICY, 'independence': independence}


def compare_rows(corpus, baseline, candidate, *, prepared=None):
    """Recompute checks from complete raw outputs, ignoring supplied scores."""
    expected = prepare_probes(corpus) if prepared is None else prepared
    errors, scores, variations = [], {}, {}
    for label, rows in (('current', baseline), ('candidate', candidate)):
        if (not isinstance(rows, list) or len(rows) != len(expected)
                or any(not isinstance(row, dict) for row in rows)
                or [row.get('id') for row in rows] != [probe['id'] for probe in expected]):
            return {'accepted': False, 'reason': 'incomplete-probes',
                    'message': 'Нужны все независимые проверки обеих моделей в одинаковом порядке.'}
        results = []
        for index, (probe, row) in enumerate(zip(expected, rows)):
            if (row.get('status') != 'measured' or row.get('task') != probe['task']
                    or row.get('caseId') != probe['caseId'] or row.get('seed') != POLICY['seed']+index
                    or row.get('backend') != 'mlx' or row.get('fallback') is not False
                    or row.get('maxTokens') != POLICY['listingMaxTokens' if probe['task'] == 'listing' else 'translationMaxTokens']
                    or row.get('temperature') != POLICY['listingTemperature' if probe['task'] == 'listing' else 'translationTemperature']
                    or not isinstance(row.get('text'), str) or not 1 <= len(row['text']) <= 20_000
                    or type(row.get('seconds')) not in (float, int)
                    or not math.isfinite(row['seconds']) or not 0 <= row['seconds'] <= POLICY['probeSeconds']+1):
                errors.append(label+':'+probe['id'])
                results.append(False)
                continue
            results.append(assess(probe, row['text']).get('passed') is True)
        scores[label] = results
        variations[label] = variation_summary(rows)
    if errors:
        return {'accepted': False, 'reason': 'incomplete-probes',
                'message': 'Сравнение прервано или часть ответов не измерена.', 'incomplete': errors}
    before, after = scores['current'], scores['candidate']
    regressions = [p['id'] for p, a, b in zip(expected, before, after) if a and not b]
    failures = [p['id'] for p, ok in zip(expected, after) if not ok]
    previous_diversity = sum(r['allThreeSurfacesDifferent'] for r in variations['current'])
    next_diversity = sum(r['allThreeSurfacesDifferent'] for r in variations['candidate'])
    improved = sum(after) > sum(before) or next_diversity > previous_diversity
    accepted = not failures and not regressions and next_diversity == len(corpus['cases']) and improved
    reason = ('accepted' if accepted else 'candidate-factual-errors' if failures else
              'repeated-variants' if next_diversity < len(corpus['cases']) else 'no-measured-improvement')
    messages = {'accepted': 'Кандидат прошёл все независимые проверки и улучшил измеряемый результат.',
                'candidate-factual-errors': 'Кандидат ошибается в проверочных фактах или формате; текущие веса сохранены.',
                'repeated-variants': 'Варианты объявления повторяются; текущие веса сохранены.',
                'no-measured-improvement': 'Измеримого улучшения нет; текущие веса сохранены.'}
    return {'accepted': accepted, 'reason': reason, 'message': messages[reason],
            'requested': len(expected), 'currentPassed': sum(before), 'candidatePassed': sum(after),
            'regressions': regressions, 'candidateFailures': failures,
            'currentDistinctCases': previous_diversity, 'candidateDistinctCases': next_diversity,
            'nativeSpeakerReviewCompleted': False,
            'limits': 'Проверка подтверждённых фактов и различий текстов не заменяет китайского редактора.'}


def validate_report(directory, report_path, *, now=None):
    report = _json(report_path)
    now = time.time() if now is None else now
    finished = report.get('finishedAt')
    if (report.get('version') != REPORT_VERSION or type(finished) not in (int, float)
            or not math.isfinite(finished) or not 0 <= now-finished <= POLICY['maximumAgeSeconds']):
        raise ValueError('Отчёт отсутствует, устарел или имеет неверную дату')
    inputs = report.get('inputs')
    if not isinstance(inputs, dict) or inputs.get('casesPath') != str(CASES.resolve()):
        raise ValueError('Для приёмки нужен штатный независимый проверочный корпус')
    fresh = capture_inputs(directory, inputs.get('candidate', {}).get('path', ''),
                           inputs.get('datasetPath', ''), CASES)
    if inputs != fresh:
        raise ValueError('После проверки изменились данные, настройки, программа или веса; нужна новая проверка')
    corpus = load_cases(CASES)
    prepared = prepare_probes(corpus, rules=active_rules(directory))
    decision = compare_rows(corpus, report.get('current'), report.get('candidate'), prepared=prepared)
    return report, decision


def record_evaluation(directory, report_path):
    """Make CLI evaluations visible in the site without changing active weights."""
    from learning_scheduler import _folder, _write
    report, decision = validate_report(directory, report_path)
    receipt = _json(Path(report['inputs']['candidate']['path'])/'trainreceipt.json', maximum=2_000_000)
    _write(_folder(directory)/'model-evaluation.json', {
        'version': 1, 'at': report['finishedAt'], 'decision': decision,
        'stepsCompleted': receipt['stepsCompleted'], 'trainedParameters': receipt['trainableParameters'],
        'candidateSha256': report['inputs']['candidate']['weightsSha256'],
        'reportSha256': _file(report_path)})
    return decision


@contextmanager
def _selection_lock(directory):
    import fcntl
    from learning_scheduler import ModelBusy, _folder
    path = _folder(directory)/'model-promotion.lock'
    descriptor = os.open(path, os.O_RDWR|os.O_CREAT|getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ModelBusy('Выбор модели уже обновляется; повторите через несколько секунд')
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def promote(directory, report_path, *, automatic=False, now=None):
    from learning_scheduler import _folder, _write, load_config, model_resource
    with _selection_lock(directory), model_resource(directory, kind='background'):
        if automatic and not load_config(directory)['automaticPromotion']:
            raise ValueError('Автоматическое принятие новых весов выключено')
        report, decision = validate_report(directory, report_path, now=now)
        if not decision['accepted']:
            return decision
        before = raw_config(directory)
        after = {**before['value'], 'adapterPath': report['inputs']['candidate']['path'],
                 'useCandidateAdapter': True}
        if before['value'].get('adapterPath') == after['adapterPath'] and before['value'].get('useCandidateAdapter') is True:
            raise ValueError('Этот адаптер уже выбран')
        journal = {'version': 1, 'state': 'prepared', 'at': time.time() if now is None else now,
                   'before': before, 'after': after, 'afterSha256': content_sha(after),
                   'previousAdapter': report['inputs']['current'], 'base': report['inputs']['base'],
                   'modelPath': report['inputs']['modelPath'],
                   'reportSha256': _file(report_path), 'decision': decision, 'automatic': automatic}
        path = _folder(directory)/'model-promotion.json'
        _write(path, journal)
        # The durable journal precedes the atomic settings switch, so even a
        # crash between these two writes leaves the previous selection usable.
        if raw_config(directory) != before:
            raise ValueError('Настройки изменились во время приёмки; повторите проверку')
        _write(Path(directory)/'text-ai.json', after)
        journal['state'] = 'accepted'
        _write(path, journal)
        return {**decision, 'selected': True, 'rollbackAvailable': True}


def rollback(directory):
    from learning_scheduler import _folder, _write, model_resource
    with _selection_lock(directory), model_resource(directory, kind='background'):
        path = _folder(directory)/'model-promotion.json'
        journal = _json(path)
        if journal.get('state') not in {'accepted', 'prepared'} or raw_config(directory)['value'] != journal.get('after'):
            raise ValueError('Выбор модели уже изменился; автоматический откат его не перезапишет')
        previous = journal.get('previousAdapter')
        if previous and adapter_identity(previous['path'], journal['modelPath']) != {k: v for k, v in previous.items() if k != 'support'}:
            raise ValueError('Предыдущие веса изменились; автоматический откат остановлен')
        if previous and previous.get('support') != _adapter_support(previous['path']):
            raise ValueError('Конфигурация предыдущего адаптера изменилась')
        base = Path(journal['modelPath'])
        if (_file(base/'config.json', 100_000) != journal['base']['configSha256']
                or {p.name: _file(p) for p in sorted(base.glob('*.safetensors'))} != journal['base']['artifacts']
                or {p.name: _file(p, 20_000_000) for p in sorted(base.iterdir()) if p.name.endswith(('.json', '.model', '.tiktoken'))} != journal['base']['support']):
            raise ValueError('Базовая модель изменилась; откат требует проверки')
        before = journal['before']
        if before.get('exists'):
            _write(Path(directory)/'text-ai.json', before['value'])
        else:
            (Path(directory)/'text-ai.json').unlink()
        journal.update(state='rolled-back', rolledBackAt=time.time())
        _write(path, journal)
        return {'rolledBack': True, 'message': 'Восстановлена предыдущая проверенная конфигурация модели.'}


def public_status(directory):
    """Read-only public summary: never return file paths or private reports."""
    path = Path(directory)/'learning/model-promotion.json'
    try:
        journal = _json(path, default={})
        current_matches = raw_config(directory)['value'] == journal.get('after')
        decision = journal.get('decision') if isinstance(journal.get('decision'), dict) else {}
        latest = _json(Path(directory)/'learning/model-evaluation.json', default={})
        measured = latest.get('decision') if isinstance(latest.get('decision'), dict) else {}
        evaluation = {key: measured.get(key) for key in ('reason', 'message', 'currentPassed', 'candidatePassed', 'requested')}
        evaluation.update(stepsCompleted=latest.get('stepsCompleted'), recordedAt=latest.get('at'))
        return {'state': journal.get('state', 'not-evaluated'),
                'rollbackAvailable': journal.get('state') in {'accepted', 'prepared'} and current_matches,
                'message': decision.get('message', 'Новые веса принимаются только после полного сравнения с текущей моделью.'),
                'candidatePassed': decision.get('candidatePassed'), 'requested': decision.get('requested'),
                'latestEvaluation': evaluation,
                'nativeSpeakerReviewCompleted': False}
    except (OSError, ValueError, TypeError):
        return {'state': 'unavailable', 'rollbackAvailable': False,
                'message': 'Состояние приёмки требует проверки.'}
