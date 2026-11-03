"""Read-only corpus provenance and leakage audit, including prompt history.

Exact leaks are rejected. Similar strings are review candidates, never proof
that two real apartments are identical. Intentional experiment mixtures retain
their original group and split so copies cannot become independent evidence.
"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import unicodedata

from text_model_contract import content_sha, load_dataset


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def payload_texts(row):
    """Inspect source, translation target and history as separate channels."""
    result = []
    for message in row['messages']:
        if message['role'] == 'system':
            continue  # Shared instructions are not independent listing facts.
        try:
            value = json.loads(message['content'])
        except ValueError:
            value = message['content']
        if isinstance(value, dict):
            for key in ('text', 'verifiedText', 'title', 'body', 'previousTextToAvoid',
                        'previousTitleToAvoid', 'recentTitlesToAvoid', 'approvedStyleExamples'):
                part = value.get(key)
                if isinstance(part, str) and part.strip():
                    result.append((key, part))
                elif isinstance(part, list):
                    result.extend((key, text) for text in part if isinstance(text, str) and text.strip())
        elif isinstance(value, str) and value.strip():
            result.append((message['role'], value))
    return result


def audit_corpora(paths, *, heldouts=(), near_threshold=.92, max_rows=10000):
    if not .8 <= near_threshold <= 1:
        raise ValueError('Near-duplicate threshold must be explicit and conservative')
    groups, exact, records, missing, errors = defaultdict(set), defaultdict(list), [], Counter(), []
    corpora, example_count = [], 0
    for path in paths:
        data = load_dataset(path)
        raw=json.loads(Path(path).read_text())
        declared={row['id']:row.get('sha256') for rows in raw['splits'].values() for row in rows}
        corpus_id = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        corpora.append({'sha256': corpus_id,
            'examples': sum(map(len, data['splits'].values()))})
        for split, rows in data['splits'].items():
            for row in rows:
                example_count += 1
                if example_count > max_rows:
                    raise ValueError('Corpus audit exceeds its explicit row budget')
                group = row['groupId']; groups[group].add(split)
                permission = row.get('permission')
                if not isinstance(permission, dict) or permission.get('trainingAllowed') is not True:
                    missing['explicitPermission'] += 1
                if not row.get('authorship'):
                    missing['explicitAuthorship'] += 1
                if not any(row.get(key) for key in ('source', 'lineage', 'provenance')):
                    missing['explicitSource'] += 1
                actual = content_sha(row['messages'])
                if declared.get(row['id']) not in (None, actual):
                    errors.append({'kind': 'content-hash', 'exampleHash': content_sha([corpus_id, row['id']])})
                if declared.get(row['id']) is None:
                    missing['declaredContentHash'] += 1
                identity = content_sha([corpus_id, row['id']])
                for channel, text in payload_texts(row):
                    normal = normalized(text)
                    if len(normal) < 40:
                        continue  # Short generic headings cannot establish apartment identity.
                    entry = {'id': identity, 'group': group, 'split': split, 'channel': channel,
                             'text': normal, 'sha': hashlib.sha256(normal.encode()).hexdigest()}
                    exact[entry['sha']].append(entry)
                    records.append(entry)
    cross_groups = sorted(group for group, splits in groups.items() if len(splits)>1)
    conflicts = []
    for digest, entries in exact.items():
        if len({entry['split'] for entry in entries}) > 1:
            conflicts.append({'kind': 'exact-cross-split-text', 'textSha256': digest,
                'examples': sorted({entry['id'] for entry in entries}),
                'channels': sorted({entry['channel'] for entry in entries})})
    heldout_overlap = []
    from rental_heldout_quality import load_cases
    for path in heldouts:
        for case in load_cases(path)['cases']:
            for key in ('sourceRu', 'referenceZh', 'generationFactsZh'):
                needle = normalized(case[key])
                hits = {entry['id'] for entry in records if needle in entry['text'] or
                        len(entry['text']) >= 100 and entry['text'] in needle}
                if hits:
                    heldout_overlap.append({'caseHash': content_sha([Path(path).name,case['id']]),
                        'field': key, 'examples': sorted(hits)})
    # Compare unique payloads, irrespective of direction or channel. Length
    # pruning cannot omit pairs whose Jaccard similarity reaches the threshold.
    unique = [{**entries[0], 'groups': {entry['group'] for entry in entries},
               'splits': {entry['split'] for entry in entries}}
              for entries in exact.values()]
    features = [{entry['text'][i:i+5] for i in range(len(entry['text'])-4)} for entry in unique]
    near, examined = [], 0
    for i, left in enumerate(unique):
        for j in range(i+1, len(unique)):
            right = unique[j]
            if left['groups']==right['groups'] and len(left['groups'])==1:
                continue
            a, b = features[i], features[j]
            if min(len(a),len(b))/max(len(a),len(b)) < near_threshold:
                continue
            examined += 1
            overlap = len(a & b)/len(a | b)
            if overlap >= near_threshold:
                near.append({'leftHash':left['sha'], 'rightHash':right['sha'],
                    'similarity':round(overlap,6),
                    'crossSplit':any(a!=b for a in left['splits'] for b in right['splits']),
                    'requiresHumanIdentityReview':True})
    passed = not (cross_groups or conflicts or errors or heldout_overlap or missing or
                  any(pair['crossSplit'] for pair in near))
    return {'version':1,'ready':passed,'corpora':corpora,'payloadsExamined':len(records),
        'uniquePayloads':len(unique),'groups':len(groups),'missingProvenance':dict(missing),
        'groupSplitConflicts':[content_sha(group) for group in cross_groups],
        'exactLeaks':conflicts,'integrityErrors':errors,'heldoutOverlaps':heldout_overlap,
        'nearDuplicates':near,'nearPairsCompared':examined,'nearThreshold':near_threshold,
        'semanticIndependenceProven':False,'rawTextsOrPrivatePathsIncluded':False}
