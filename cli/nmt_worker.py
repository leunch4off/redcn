#!/usr/bin/env python3
"""Isolated, local Russian → English → Chinese review preview.

JSON stdin → JSON stdout. --status never imports the native model runtime.
Does not save cards or change source facts. See docs/LOCAL-TRANSLATION.md.
"""
import argparse
from collections import Counter
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

MODEL_NAMES = ('translate-ru_en-1_9', 'translate-en_zh-1_9')
MAX_TEXT = 5000


def models_directory(value=None):
    return Path(value or os.environ.get('REDCN_TRANSLATION_MODELS') or
                Path.home()/'.cache/redcn/translation-models').expanduser()


def runtime_status(directory):
    missing = []
    for name in MODEL_NAMES:
        for part in ('model/model.bin', 'model/config.json', 'sentencepiece.model', 'metadata.json'):
            if not (directory/name/part).is_file():
                missing.append(name+'/'+part)
    dependencies = {name: importlib.util.find_spec(name) is not None
                    for name in ('ctranslate2', 'sentencepiece')}
    return {'available': not missing and all(dependencies.values()),
            'backend': 'nmt-pivot', 'needsReview': True, 'modelsDirectory': str(directory),
            'missing': missing, 'dependencies': dependencies, 'maxCharacters': MAX_TEXT,
            'route': ['ru', 'en', 'zh'], 'licenseNotice':
            'EN→ZH: upstream OPUS model CC-BY-4.0. RU→EN: overall model license not explicitly stated in upstream package; provenance notices retained.'}


def source_segments(text):
    """Keep paragraph and sentence separators; cap each inference input by spaces."""
    pieces = []
    for part in re.split(r'(\n+|(?<=[.!?。！？])\s+)', text):
        if not part:
            continue
        if part.isspace():
            pieces.append((False, part)); continue
        if len(part) <= 700:
            pieces.append((True, part)); continue
        words = re.findall(r'\S+\s*', part)
        chunk = ''
        for word in words:
            if chunk and len(chunk)+len(word) > 700:
                pieces.append((True, chunk.rstrip())); pieces.append((False, ' ')); chunk = ''
            chunk += word
        if chunk:
            pieces.append((True, chunk))
    return pieces


def numbers(text):
    # Chinese renders common amounts as 8万 / 八万 and quantities as 两间.
    def canonical(value):
        value = format(Decimal(value), 'f')
        return value.rstrip('0').rstrip('.') if '.' in value else value
    def chinese_value(value):
        digits = {'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
        small = {'十':10,'百':100,'千':1000}; section = 0; number = 0; total = 0
        for char in value:
            if char in digits: number = digits[char]
            elif char in small: section += (number or 1)*small[char]; number = 0
            else:
                section += number; number = 0
                if char=='万': total += (section or 1)*10000
                elif char=='亿': total = (total+section or 1)*100000000
                section = 0
        return str(total+section+number)
    russian = {'ноль':0,'один':1,'одна':1,'одно':1,'одну':1,'одной':1,'одного':1,
        'два':2,'две':2,'двух':2,'три':3,'трех':3,'трёх':3,'четыре':4,'четырех':4,
        'пять':5,'шесть':6,'семь':7,'восемь':8,'девять':9,'десять':10,
        'одиннадцать':11,'двенадцать':12,'тринадцать':13,'четырнадцать':14,'пятнадцать':15,
        'шестнадцать':16,'семнадцать':17,'восемнадцать':18,'девятнадцать':19,
        'двадцать':20,'тридцать':30,'сорок':40,'пятьдесят':50,'шестьдесят':60,
        'семьдесят':70,'восемьдесят':80,'девяносто':90,'сто':100,'двести':200,
        'триста':300,'четыреста':400,'пятьсот':500,'шестьсот':600,'семьсот':700,
        'восемьсот':800,'девятьсот':900,'тысяча':1000,'тысячи':1000,'тысяч':1000,
        'миллион':1000000,'миллиона':1000000,'миллионов':1000000}
    word = '(?:'+'|'.join(sorted(russian,key=len,reverse=True))+')'
    def russian_value(match):
        total = 0; section = 0
        for part in match[0].lower().split():
            number = russian[part]
            if number>=1000: total += (section or 1)*number; section = 0
            else: section += number
        return str(total+section)
    text = re.sub(r'(\d+(?:[.,]\d+)?)\s*(тыс(?:яч[аи]?)?\.?|тысяч|миллион(?:а|ов)?)\b',
                  lambda m: canonical(Decimal(m[1].replace(',', '.'))*(1000000 if m[2].startswith('миллион') else 1000)),text,flags=re.I)
    text = re.sub(r'\b'+word+r'(?:\s+'+word+r')*\b',russian_value,text,flags=re.I)
    text = re.sub(r'(\d+(?:[.,]\d+)?)\s*([万千亿])',
                  lambda m: canonical(Decimal(m[1].replace(',', '.'))*{'万':10000,'千':1000,'亿':100000000}[m[2]]), text)
    text = re.sub(r'[零一二两三四五六七八九十百千万亿]+(?=间|个|名|位|人|层|楼|米|月|年|天|周|室|房|把|张|台|件|平方米|卢布|元|岁)',
                  lambda m: chinese_value(m[0]), text)
    values = re.findall(r'\d+(?:[ \u00a0\u202f,\.]\d+)*', text)
    result = []
    for value in values:
        value = re.sub(r'[ \u00a0\u202f]', '', value)
        value = re.sub(r'[,.](?=\d{3}(?:[,.]|$))', '', value)
        result.append(canonical(value.replace(',', '.')))
    return Counter(result)


def audit(source, translated):
    before, after = numbers(source), numbers(translated)
    missing, extra = list((before-after).elements()), list((after-before).elements())
    source_negative = bool(re.search(r'\b(?:не|нет|нельзя|запрещ\w*|без|отсутств\w*)\b', source, re.I))
    target_negative = bool(re.search(r'不|无|没|未|禁止|不可|不得|免', translated))
    conditional = bool(re.search(r'\b(?:если|возможно|может|согласован\w*|согласованию|обсуждается)\b', source, re.I))
    target_conditional = bool(re.search(r'如果|若|可能|可以|可|协商|商议|同意|讨论', translated))
    names = sorted(set(re.findall(r'\b[А-ЯЁ][а-яё]+(?:[- ][А-ЯЁ][а-яё]+)*', source)))
    warnings = ['Машинный перевод через английский: проверьте смысл, названия мест и условия перед применением.']
    if missing or extra:
        warnings.append('Числа не совпали с исходным текстом. Не применяйте перевод до проверки сумм, площадей и сроков.')
    if source_negative and not target_negative:
        warnings.append('В исходнике есть отрицание, которое не удалось подтвердить в переводе. Проверьте запреты и отсутствие оснащения.')
    if conditional and not target_conditional:
        warnings.append('В исходнике есть условие; его сохранение не подтверждено. Проверьте договорённости.')
    return {'numbersPreserved': not missing and not extra, 'missingNumbers': missing,
            'extraNumbers': extra, 'sourceNegation': source_negative,
            'targetNegationDetected': target_negative, 'sourceConditional': conditional,
            'targetConditionalDetected': target_conditional,
            'properNamesToReview': names, 'properNamesVerified': False,
            'automaticSemanticVerification': False}, warnings


def user_glossary_spans(text, rules):
    if not isinstance(rules, dict):
        raise ValueError('Словарь должен быть JSON-объектом')
    candidates = [(key, value) for key, value in rules.items()
                  if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip()
                  and len(key)<=300 and len(value)<=1000]
    candidates.sort(key=lambda row: len(row[0]), reverse=True)
    if len(candidates)>20_000:
        raise ValueError('Слишком много правил перевода')
    by_first = {}
    for key, value in candidates:
        by_first.setdefault(key[0].casefold(), []).append((key, value))
    spans = []; pending = ''; cursor = 0
    while cursor<len(text):
        matched = None
        for key, value in by_first.get(text[cursor].casefold(), []):
            part = text[cursor:cursor+len(key)]
            if part.casefold()!=key.casefold(): continue
            if cursor and text[cursor-1].isalnum() and key[0].isalnum(): continue
            if cursor+len(key)<len(text) and text[cursor+len(key)].isalnum() and key[-1].isalnum(): continue
            matched = (key, value); break
        if matched:
            if pending: spans.append((False, pending)); pending = ''
            spans.append((True, matched[1])); cursor += len(matched[0])
        else:
            pending += text[cursor]; cursor += 1
    if pending: spans.append((False, pending))
    return spans


# These are source permission clauses, not amenities inferred from photos.
CONDITION_RULES = {
    'можно с детьми':'可带孩子入住', 'с детьми можно':'可带孩子入住',
    'можно с ребенком':'可带孩子入住', 'с ребенком можно':'可带孩子入住',
    'дети разрешены':'可带孩子入住', 'с детьми разрешено':'可带孩子入住',
    'с детьми допускается':'可带孩子入住', 'дети допускаются':'可带孩子入住',
    'проживание с детьми разрешено':'可带孩子入住',
    'нельзя с детьми':'不接受携带儿童入住', 'с детьми нельзя':'不接受携带儿童入住',
    'без детей':'不接受携带儿童入住', 'дети запрещены':'不接受携带儿童入住',
    'с детьми запрещено':'不接受携带儿童入住', 'с детьми не разрешено':'不接受携带儿童入住',
    'проживание с детьми запрещено':'不接受携带儿童入住',
    'можно с животными':'可带宠物入住', 'с животными можно':'可带宠物入住',
    'можно с питомцами':'可带宠物入住', 'с питомцами можно':'可带宠物入住',
    'можно с питомцем':'可带宠物入住', 'животные разрешены':'可带宠物入住',
    'питомцы разрешены':'可带宠物入住', 'с животными разрешено':'可带宠物入住',
    'с животными нельзя':'不允许携带宠物入住', 'нельзя с животными':'不允许携带宠物入住',
    'с питомцами нельзя':'不允许携带宠物入住', 'с питомцем нельзя':'不允许携带宠物入住',
    'нельзя с питомцами':'不允许携带宠物入住', 'животные запрещены':'不允许携带宠物入住',
    'питомцы запрещены':'不允许携带宠物入住', 'без животных':'不允许携带宠物入住',
    'без питомцев':'不允许携带宠物入住', 'с животными запрещено':'不允许携带宠物入住',
    'с животными не разрешено':'不允许携带宠物入住',
    'проживание с животными запрещено':'不允许携带宠物入住',
    'животные по согласованию':'宠物入住需经房东同意',
    'с животными по согласованию':'宠物入住需经房东同意',
    'можно с животными по согласованию':'宠物入住需经房东同意',
    'с животными можно по согласованию':'宠物入住需经房东同意',
    'с детьми по согласованию':'携带儿童入住需经房东同意',
    'можно с детьми и животными':'可带孩子和宠物入住',
    'можно с животными и детьми':'可带孩子和宠物入住',
    'с детьми и животными можно':'可带孩子和宠物入住',
    'нельзя с детьми и животными':'不接受携带儿童或宠物入住',
    'с детьми и животными нельзя':'不接受携带儿童或宠物入住',
    'без детей и животных':'不接受携带儿童或宠物入住',
    'можно ли с детьми':'可以带孩子入住吗？',
    'можно ли с животными':'可以带宠物入住吗？',
    'можно ли с питомцем':'可以带宠物入住吗？',
}


def permission_clauses(text):
    result = []
    for piece in re.split(r'([,;.!?。！？\n]+)', text):
        if not piece: continue
        raw = piece.strip(); key = re.sub(r'\s+', ' ', raw.lower().replace('ё','е'))
        conjunction = ''
        if key.startswith('но '): conjunction = '但'; key = key[3:]
        known = CONDITION_RULES.get(key)
        people = bool(re.search(r'дет(?:и|ей|ьми)|ребен|ребён|животн|питом|кошк|кошек|собак|кот(?:ом|ами|ы|а)?\b', key))
        permission = bool(re.search(r'можно|нельзя|разреш|запре|без |допуск|соглас|обсужд|рассмотр|ли\b|только|если|не ', key))
        uncertain = people and (permission or bool(re.fullmatch(r'(?:с|со) .{1,40}',key))) and not known
        if known or uncertain:
            left = piece[:len(piece)-len(piece.lstrip())]
            right = piece[len(piece.rstrip()):]
            if left: result.append((False,left,None))
            result.append((True,conjunction+known if known else raw,raw if uncertain else None))
            if right: result.append((False,right,None))
        else: result.append((False,piece,None))
    return result


def glossary_spans(text, rules):
    spans = []
    for literal, value, unknown in permission_clauses(text):
        children = [(True,value)] if literal else user_glossary_spans(value,rules)
        for protected, content in children:
            if spans and not protected and not spans[-1][0]:
                spans[-1] = (False,spans[-1][1]+content)
            else: spans.append((protected,content))
    return spans


def protect_rules(text, rules):
    rendered = []; replacements = []
    for literal, value in glossary_spans(text, rules):
        if not literal:
            rendered.append(value); continue
        index = len(replacements); suffix = ''
        while True:
            suffix = chr(65+index%26)+suffix; index = index//26-1
            if index<0: break
        marker = 'RZXQ'+suffix+'QXZR'
        if marker in text.upper(): raise ValueError('Текст содержит служебный маркер перевода')
        rendered.append(' '+marker+' '); replacements.append((marker, value))
    return ''.join(rendered), replacements


def restore_rules(text, replacements):
    for marker, value in replacements:
        expression = r'\s*'.join(re.escape(char) for char in marker)
        text, count = re.subn(expression, lambda match: value, text, flags=re.I)
        if count != 1:
            raise ValueError('Модель изменила точное правило словаря. Сохранён безопасный предварительный перевод; попробуйте более короткий текст.')
    return text


def model_batches(originals, directory):
    if not originals: return []
    import ctranslate2
    import sentencepiece
    translated = originals
    for name in MODEL_NAMES:
        package = directory/name
        tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(package/'sentencepiece.model'))
        translator = ctranslate2.Translator(str(package/'model'), device='cpu', compute_type='int8',
                                           inter_threads=1, intra_threads=2)
        try:
            tokens = [tokenizer.encode(part, out_type=str) for part in translated]
            if any(len(row)>400 for row in tokens):
                raise ValueError('Слишком длинное предложение. Разбейте текст на более короткие предложения.')
            outputs = translator.translate_batch(tokens, beam_size=3, max_batch_size=16,
                        max_input_length=0, max_decoding_length=768, repetition_penalty=1.05)
            translated = [tokenizer.decode(output.hypotheses[0]).replace('▁',' ').strip() for output in outputs]
        finally:
            translator.unload_model()
        del translator
    try:
        from opencc import OpenCC
        converter = OpenCC('t2s')
        translated = [converter.convert(part) for part in translated]
    except ImportError:
        pass
    return translated


def translate_pieces(pieces, directory):
    translated = iter(model_batches([value for active, value in pieces if active], directory))
    return ''.join(next(translated) if active else value for active, value in pieces)


def translate_glossary_fragments(text, rules, directory):
    pieces = []
    for literal, value in glossary_spans(text, rules):
        if literal:
            pieces.append((False, value)); continue
        # Keep original whitespace around glossary boundaries and paragraph gaps.
        for active, part in source_segments(value):
            if not active: pieces.append((False, part)); continue
            match = re.fullmatch(r'(\s*)(.*?)(\s*)', part, re.S)
            if match[1]: pieces.append((False, match[1]))
            if match[2]:
                # Punctuation-only fragments carry no words to translate.
                pieces.append((bool(re.search(r'\w', match[2])), match[2]))
            if match[3]: pieces.append((False, match[3]))
    return translate_pieces(pieces, directory)


def translate_request(payload, directory):
    text = payload.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or '\x00' in text:
        raise ValueError('Введите от 1 до 5000 символов для локального машинного перевода')
    status = runtime_status(directory)
    if not status['available']:
        raise ValueError('Локальный переводчик не установлен: запустите scripts/setup-translation-runtime.py')
    started = time.monotonic(); rules = payload.get('rules', {})
    protected, glossary = protect_rules(text, rules)
    fragmented = False
    try:
        rendered = restore_rules(translate_pieces(source_segments(protected), directory), glossary)
    except ValueError:
        if not glossary: raise
        rendered = translate_glossary_fragments(text, rules, directory)
        fragmented = True
    checks, warnings = audit(text, rendered)
    unknown_conditions = [unknown for _,_,unknown in permission_clauses(text) if unknown]
    checks['protectedUnknownConditions'] = unknown_conditions
    if unknown_conditions:
        warnings.append('Неоднозначные условия о детях или животных сохранены по-русски: переведите и проверьте их вручную.')
    if fragmented:
        warnings.append('Точные термины словаря сохранены отдельно; остальной текст переведён фрагментами. Проверьте связность и порядок слов.')
    return {'text': rendered, 'backend': 'nmt-pivot', 'needsReview': True,
            'route': ['ru', 'en', 'zh'], 'warnings': warnings, 'checks': checks,
            'elapsedSeconds': round(time.monotonic()-started, 2), 'fragmentedGlossaryFallback': fragmented,
            'coverage': None, 'sourceFactsUpdated': False, 'exactRulesPreserved': len(glossary)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--models')
    args = parser.parse_args()
    directory = models_directory(args.models)
    try:
        if args.status:
            result = runtime_status(directory)
        else:
            raw = sys.stdin.buffer.read(1_000_001)
            if len(raw)>1_000_000:
                raise ValueError('Запрос слишком большой')
            payload = json.loads(raw.decode('utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('Ожидается JSON-объект')
            result = translate_request(payload, directory)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'error': str(error), 'backend': 'nmt-pivot', 'needsReview': True}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
