#!/usr/bin/env python3
"""Rebuild a review-only Russian/Chinese lexical index from Wiktionary extracts.

Requires opencc-python-reimplemented only when rebuilding; no runtime dependency.
Raw source downloads are kept outside the repository. No model-generated pairs.
"""
import argparse
import collections
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import urllib.parse
import urllib.request

SOURCES = (
    ('zh-simplified', 'zhwiktionary', '俄语', 'kaikki.org-dictionary-俄语.jsonl'),
    ('zh-traditional', 'zhwiktionary', '俄語', 'kaikki.org-dictionary-俄語.jsonl'),
    ('ru-mandarin', 'ruwiktionary', 'Китайский (Гуаньхуа)', 'kaikki.org-dictionary-КитайскийГуаньхуа.jsonl'),
)
HAN = re.compile(r'[\u3400-\u9fff\U00020000-\U0002ffff]')
CYR = re.compile(r'[А-Яа-яЁё]')


def source_url(edition, language, filename):
    return ('https://kaikki.org/' + edition + '/' + urllib.parse.quote(language, safe='')
            + '/' + urllib.parse.quote(filename, safe=''))


def fetch_sources(folder):
    folder.mkdir(parents=True, exist_ok=True)
    def fetch(spec):
        key, edition, language, filename = spec
        path = folder / (key + '.jsonl')
        url = source_url(edition, language, filename)
        if not path.is_file():
            temporary = path.with_suffix('.part')
            with urllib.request.urlopen(url, timeout=45) as response, temporary.open('wb') as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    if output.tell() > 160_000_000:
                        raise ValueError('Dictionary source exceeds the 160 MB bound')
            temporary.replace(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'id': key, 'edition': edition, 'url': url, 'bytes': path.stat().st_size,
                'sha256': digest, 'path': str(path)}
    return list(concurrent.futures.ThreadPoolExecutor(3).map(fetch, SOURCES))


def clean_ru(value):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFC', value).replace('\u0301', '')
    value = re.sub(r'\s+', ' ', value).strip(' ;,')
    if not 2 <= len(value) <= 90 or not CYR.search(value):
        return None
    if HAN.search(value) or re.search(r'[\n.!?<>={}\[\]«»“”"◆]', value):
        return None
    return value


def clean_zh(value, converter):
    if not isinstance(value, str):
        return None
    value = converter.convert(unicodedata.normalize('NFC', value))
    value = re.sub(r'\s+', ' ', value).strip(' 。；，、')
    if not 1 <= len(value) <= 50 or not HAN.search(value):
        return None
    if CYR.search(value) or re.search(r'[<>={}\[\]◆]', value):
        return None
    return value


def extract(folder, sources):
    try:
        from opencc import OpenCC
    except ImportError as error:
        raise SystemExit('Rebuild requires: python -m pip install opencc-python-reimplemented') from error
    converter = OpenCC('t2s')
    pairs = {}
    attested_forms = set()
    counts = collections.Counter()
    def add(ru, zh, source, page):
        ru, zh = clean_ru(ru), clean_zh(zh, converter)
        if ru and zh:
            # Accent normalization and exact duplicate removal do not create senses.
            pairs.setdefault((ru, zh), (source, page))
    for source in sources:
        path = folder / (source['id'] + '.jsonl')
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                counts[source['id']] += 1
                word = row.get('word', '')
                if not isinstance(word, str):
                    continue
                if source['edition'] == 'zhwiktionary':
                    if row.get('lang_code') == 'ru':
                        for sense in row.get('senses', []):
                            if set(sense.get('tags', [])) & {'form-of', 'no-gloss'}:
                                continue
                            for gloss in sense.get('glosses', []):
                                add(word, gloss, source['id'], word)
                        lemma = clean_ru(word)
                        if lemma:
                            for form in row.get('forms', []):
                                value = clean_ru(form.get('form'))
                                if (value and value.casefold().replace('ё', 'е') != lemma.casefold().replace('ё', 'е')
                                        and not set(form.get('tags', [])) & {'romanization', 'transliteration'}):
                                    attested_forms.add((value, lemma))
                    # Explicit ru translations of a Chinese headword are stronger
                    # than an inferred bridge through English and stay bilingual.
                    elif row.get('lang_code') == 'zh':
                        for translation in row.get('translations', []):
                            if translation.get('lang_code') == 'ru':
                                add(translation.get('word'), word, source['id'], word)
                elif row.get('lang_code') in ('zh', 'cmn'):
                    for sense in row.get('senses', []):
                        if set(sense.get('tags', [])) & {'form-of', 'no-gloss'}:
                            continue
                        for gloss in sense.get('glosses', []):
                            # Only explicit semicolon-delimited meanings are split.
                            for meaning in re.split(r';\s*', gloss):
                                add(meaning, word, source['id'], word)
    entries = [[ru, zh, *pairs[(ru, zh)]] for ru, zh in sorted(pairs, key=lambda pair: (pair[0].casefold(), pair[1]))]
    by_ru = collections.Counter(ru.casefold().replace('ё', 'е') for ru, *_rest in entries)
    lemmas = {ru.casefold().replace('ё', 'е'): ru for ru, *_rest in entries}
    forms = sorted({(form, lemmas[lemma.casefold().replace('ё', 'е')]) for form, lemma in attested_forms
                    if lemma.casefold().replace('ё', 'е') in lemmas})
    form_keys = {form.casefold().replace('ё', 'е') for form, _lemma in forms}
    return entries, forms, {'entries': len(entries), 'russianKeys': len(by_ru),
        'chineseForms': len({row[1] for row in entries}),
        'ambiguousRussianKeys': sum(n > 1 for n in by_ru.values()),
        'attestedFormLinks': len(forms), 'attestedFormKeys': len(form_keys),
        'uniqueLookupKeys': len(set(by_ru) | form_keys),
        'reviewedEntries': 0, 'sourceRecords': dict(counts)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-dir', type=Path, default=Path('/tmp/redcn-ruzh-sources'))
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'data/ru-zh-open-lexicon.json')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--retrieved-at', default='2026-10-30')
    args = parser.parse_args()
    if args.download:
        sources = fetch_sources(args.source_dir)
    else:
        sources = []
        for key, edition, language, filename in SOURCES:
            path = args.source_dir / (key + '.jsonl')
            sources.append({'id': key, 'edition': edition, 'url': source_url(edition, language, filename),
                'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    entries, forms, stats = extract(args.source_dir, sources)
    for source in sources:
        source.pop('path', None)
        source['pageUrlPrefix'] = 'https://' + ('zh' if source['edition']=='zhwiktionary' else 'ru') + '.wiktionary.org/wiki/'
    result = {'version': 1, 'kind': 'review-only-open-lexicon', 'languagePair': ['ru', 'zh-Hans'],
        'retrievedAt': args.retrieved_at, 'reviewed': False,
        'entrySchema': ['ru', 'zh', 'sourceId', 'sourcePage'], 'formSchema': ['attestedRussianForm','russianLemma'], 'stats': stats,
        'license': 'CC-BY-SA-4.0', 'licenseUrl': 'https://creativecommons.org/licenses/by-sa/4.0/',
        'attribution': 'Wiktionary contributors; extracts by Kaikki.org / Wiktextract (Tatu Ylonen).',
        'changes': ['Selected Russian headwords with Chinese glosses and Chinese headwords with Russian glosses.',
            'Removed explicit form-of senses, long definitions and exact duplicate pairs.',
            'Removed Russian stress accents; converted Traditional Chinese to Simplified with OpenCC t2s.'],
        'sources': sources, 'entries': entries, 'forms': forms}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps({'path': str(args.output), 'bytes': args.output.stat().st_size, **stats}, ensure_ascii=False))


if __name__ == '__main__':
    main()
