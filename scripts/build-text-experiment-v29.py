#!/usr/bin/env python3
"""Freeze two explicit mixtures and a new synthetic holdout before inference.

The training situations are the already checked project-authored v28 corpus.
The new holdout is authored independently here, is never used as a target, and
is labelled synthetic/AI-authored rather than native-speaker adjudicated.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from text_model_contract import content_sha, load_dataset, validate_dataset
from rental_heldout_quality import check_training_disjoint, load_cases


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def holdout():
    pairs = [('холодильник', '冰箱'), ('стиральная машина', '洗衣机'),
             ('духовой шкаф', '烤箱'), ('микроволновая печь', '微波炉'),
             ('посудомоечная машина', '洗碗机'), ('кондиционер', '空调'),
             ('диван', '沙发'), ('письменный стол', '书桌')]
    cases = []
    for i in range(30):
        rent, deposit = 54235+i*1379, 21875+i*863
        area = format(27.4+i*.7, '.1f')
        first, second = 'КонтрольЯнтарь'+str(i+1), 'КонтрольНефрит'+str(i+1)
        present, absent, unknown = pairs[i%8], pairs[(i+3)%8], pairs[(i+5)%8]
        pet_ru, pet_zh = (('Животные запрещены.', '不接受宠物。'),
                         ('Животные только по согласованию.', '携带宠物需事先协商。'),
                         ('Условия для животных неизвестны.', '宠物条件待确认。'))[i%3]
        owner_ru, owner_zh = (('Владелец не оформляет временную регистрацию.', '房东不办理临时居住登记。'),
                             ('Условия регистрации у владельца неизвестны.', '房东办理临时居住登记的条件尚未确认。'),
                             ('Владелец оформляет временную регистрацию за отдельную плату.', '房东可协助办理临时居住登记，费用另付。'))[i%3]
        ru = (f'Аренда {rent} рублей в месяц. Залог {deposit} рублей. Площадь {area} м². '
              f'В квартире есть {present[0]}. В комплектации отсутствует: {absent[0]}. '
              f'Не подтверждено наличие: {unknown[0]}. {pet_ru} {owner_ru} '
              f'Субаренда запрещена. Вода и электричество оплачиваются по счётчикам отдельно. '
              f'В исходнике названы станции {first} и {second}; время в дороге неизвестно.')
        zh = (f'月租{rent}卢布。押金{deposit}卢布。面积{area}㎡。'
              f'已确认配有{present[1]}。未配备{absent[1]}。{unknown[1]}是否配备尚未确认。'
              f'{pet_zh}{owner_zh}禁止转租。水电按表另付。'
              f'原文提及{first}和{second}站，通勤时间尚未确认。')
        cases.append({'id': f'heldout-{i+1:02d}', 'groupId': f'independent-v29-{i+1:02d}',
            'sourceRu': ru, 'referenceZh': zh, 'generationFactsZh': zh,
            'fields': {'price': str(rent), 'area': area, 'metro': first, 'priceCurrency': 'RUB'},
            'observedStations': [{'ru': n, 'aliases': [n]} for n in (first, second)],
            'sourceClauseSha256': hashlib.sha256(ru.encode()).hexdigest(),
            'conflicts': [], 'annotationNotes': ['Independent fictional situation; exact station labels are invented.',
                'Owner registration and any studio service must remain distinct.'],
            'permission': {'trainingAllowed': False, 'evaluationAllowed': True, 'basis': 'project-authored'},
            'authorship': 'project-authored-synthetic', 'nativeSpeakerReviewed': False})
    return {'version': 1, 'id': 'rental-independent-synthetic-v29',
        'source': 'Independent project-authored fictional situations, not copied from v28 training or model answers',
        'annotation': {'author': 'Codex AI-assisted independent annotation', 'nativeSpeakerReview': False,
            'trainingUseAllowed': False, 'checksFrozenBeforeInference': True,
            'sourceStationEvidence': 'invented literal labels; not official station translations'},
        'limitations': ['Synthetic automatic acceptance only; no native-speaker certification.',
            'After first evaluation this set becomes diagnostic; a future final blind acceptance needs new groups.'],
        'cases': cases}


def main():
    source_path = ROOT/'data/learning/factual-curriculum-v28.json'
    source = load_dataset(source_path)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    heldout_path = ROOT/'data/quality/rental-heldout-v29.json'
    heldout_sha = write(heldout_path, holdout())
    independent = load_cases(heldout_path)
    artifacts, counts = {}, {}
    for name in ('advertising-only', 'translation-retention'):
        splits = {}
        for split in ('train', 'validation'):
            rows = source['splits'][split]
            if split == 'train' and name == 'advertising-only':
                rows = [r for r in rows if r['task'] == 'advertising']
            splits[split] = [{**row,
                'permission': {'trainingAllowed': True, 'basis': 'project-authored', 'owner': 'redcn-project'},
                'authorship': 'project-authored-synthetic',
                'lineage': {'corpus': 'factual-curriculum-v28', 'sourceExampleId': row['id'],
                    'sourceExampleSha256': content_sha(row['messages'])}}
                for row in rows]
        value = validate_dataset({'version': 1, 'provenance': {**source['provenance'],
            'experiment': 'v29-two-mixture-retention', 'mixture': name,
            'derivedFromSha256': source_sha, 'newTrainingSituations': False,
            'nativeSpeakerReviewCompleted': False,
            'permission': 'project-authored examples; no user cards or model outputs'}, 'splits': splits})
        check_training_disjoint(independent, value)
        relative = 'data/learning/text-v29-'+name+'.json'
        artifacts[relative] = write(ROOT/relative, value)
        counts[name] = {s: {'examples': len(rows), 'groups': len({r['groupId'] for r in rows}),
            'tasks': {t: sum(r['task']==t for r in rows) for t in ('advertising', 'translation-ru-zh', 'translation-zh-ru')}}
            for s, rows in splits.items()}
    artifacts[str(heldout_path.relative_to(ROOT))] = heldout_sha
    protocol = {'version': 1, 'id': 'v29-two-mixture-retention', 'frozenBeforeTraining': True,
        'authoredAt': '2026-11-02', 'question': 'Does retaining bilingual examples reduce translation regression at the same bounded step budget?',
        'base': 'mlx-community/Qwen2.5-0.5B-Instruct-4bit',
        'initialAdapterSha256': '3b5a50a32bb1485eee9f6501ed870b822d57e0bbc22bd8704551977e79d22581',
        'baseConfigSha256': 'b045e57ea90b8f1b35f89f954b176a5c1faa02bd0af2c89bcec191239d66cef4',
        'artifacts': artifacts, 'counts': counts, 'independentGroups': 30,
        'builderSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'recipe': {'steps': 240, 'seed': 20261102, 'learningRate': .00002,
            'maxSequenceLength': 1024, 'evalExamples': 32, 'sampling': 'shuffled',
            'batchSize': 1, 'maxSecondsPerRun': 900, 'mlxMemoryLimitBytes': 3500000000},
        'evaluation': {'directions': ['ru-zh', 'zh-ru'], 'advertisingVariants': 3,
            'listingMaxTokens': 768, 'translationMaxTokens': 512, 'probeSeconds': 55,
            'maxSecondsPerComparison': 1800, 'topP': .9, 'listingTemperature': .75,
            'translationTemperature': 0, 'seed': 20261102, 'fallbackCredited': False,
            'acceptance': 'All direct candidate facts pass, no baseline regression, complete probes, distinct variants, strict measured improvement.',
            'modelAndRendererContributionReportedSeparately': True},
        'safety': {'offline': True, 'foregroundPriority': True, 'separateOutputDirectories': True,
            'selectedWeightsUnchangedUntilIndependentAdmission': True},
        'limitations': ['Same step count does not imply equal per-task exposure; actual sampled counts must be reported.',
            'Reuse of v28 authored train situations is explicit; this is a mixture experiment, not a new independent training corpus.',
            'Synthetic bilingual references are not independently approved by a native editor.']}
    path = ROOT/'data/quality/text-experiment-v29.json'
    sha = write(path, protocol)
    print(json.dumps({'artifacts': artifacts, 'protocolSha256': sha, 'counts': counts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
