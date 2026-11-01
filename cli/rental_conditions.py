"""Conservative fee-scope guard, not an unrestricted semantic verifier.

This is a post-hoc runtime improvement from a real failed heldout answer. The
original frozen benchmark is kept unchanged; the guard never rewrites prose.
"""
import re

FEES = {
    'water': (r'\bвод(?:а|ы|у|ой|е|оснабжен\w*)\b', r'水(?:电)?费|水电|水费'),
    'electricity': (r'\bэлектричеств\w*|\bэлектроэнерги\w*|\bсвет\b', r'水电(?:费)?|电费'),
    'internet': (r'\bинтернет\w*|\bсеть\b', r'网费|网络(?:费)?|宽带(?:费)?'),
    'building': (r'\bсодержани\w*\s+дом\w*|\bобслуживани\w*\s+дом\w*', r'物业费'),
    'utilities': (r'\bкоммунальн\w*\s+(?:платеж\w*|услуг\w*|расход\w*)|\bжк[ух]\b', r'公用事业(?:费|费用)?|公共杂费|公共物业费'),
    'meters': (r'\bсч[её]тчик\w*|\bпоказани\w*\s+прибор\w*', r'表计(?:费用|费)?'),
}

MARKERS = {
    'ru': (
        ('unknown', r'не\s+подтвержд\w*|уточня\w*|неизвест\w*|по\s+согласованию'),
        ('metered', r'по\s+(?:показани\w*\s+)?сч[её]тчик\w*|по\s+потреблени\w*'),
        ('separate', r'не\s+включ[её]н\w*|не\s+вход\w*|(?:оплат\w*|оплачива\w*|плат\w*)\s+отдельно|отдельно|дополнительно|сверх\s+аренд\w*'),
        ('included', r'(?<!не )включ[её]н\w*|(?<!не )вход\w*\s+в\s+(?:стоимост\w*|аренд\w*)|без\s+доплат\w*(?:\s+по\s+сч[её]тчик\w*)?|дополнительн\w*\s+платеж\w*\s+по\s+сч[её]тчик\w*\s+нет|по\s+сч[её]тчик\w*\s+не\s+(?:нужно|надо)\s+доплачивать|не\s+нужн\w*\s+доплачивать'),
    ),
    'zh': (
        ('unknown', r'尚未确认|未被确认|未被证实|未确认|未核实|需(?:要)?确认|待确认|待核实|不确定|协商'),
        ('metered', r'按表|按用量|按实际用量|按使用量|抄表'),
        ('separate', r'不包括|不包含|不含|另付|另计|另外支付|另行支付|单独支付|需自付|自付'),
        ('included', r'(?<!不)包括|(?<!不)包含|(?<!不)已含|(?<!不)已包含|包水电|租金含|全包|无需另付'),
    ),
}


def fee_claims(text, language):
    """Extract only explicit fee relations with a nearby scoped predicate.

    A generic utility fee is not silently equated with water/electricity: an
    owner can include building charges and still charge meters separately.
    """
    if language not in MARKERS:
        raise ValueError('Неверный язык условий аренды')
    result = {}
    for sentence in re.split(r'[。；;.!?！？\n]+', text):
        for clause in re.split(r'[,，]', sentence):
            markers = [(marker.start(), marker.end(), policy)
                       for policy, pattern in MARKERS[language]
                       for marker in re.finditer(pattern, clause, re.I)]
            # e.g. 无需另付 includes the shorter 另付 but means the opposite.
            markers = [row for row in markers if not any(
                other[0] <= row[0] and row[1] <= other[1]
                and other[1]-other[0] > row[1]-row[0] for other in markers)]
            for category, patterns in FEES.items():
                for match in re.finditer(patterns[1 if language == 'zh' else 0], clause, re.I):
                    # Fees connected by a list can share the following verb.
                    # Limit the window so an unrelated distant clause cannot
                    # turn an unknown condition into a confirmed promise.
                    candidates = []
                    for left, right, policy in markers:
                        distance = max(0, match.start()-right, left-match.end())
                        if distance <= 65:
                            candidates.append((distance, policy))
                    if not candidates:
                        continue
                    distance = min(item[0] for item in candidates)
                    nearby = {policy for value, policy in candidates if value == distance}
                    # Negative expressions contain a positive substring in
                    # Russian. Explicit exclusion wins that lexical overlap.
                    policy = next((value for value in ('unknown', 'metered', 'separate', 'included') if value in nearby), None)
                    result.setdefault(category, set()).add(policy)
    return result


def fee_issues(source, output, source_language='zh', output_language='zh', *, require_mentions=False):
    before = fee_claims(source, source_language)
    after = fee_claims(output, output_language)
    issues = []
    for category, policies in after.items():
        if category not in before:
            issues.append('Добавлено неподтверждённое условие оплаты: '+category)
        elif not policies <= before[category]:
            issues.append('Изменено условие оплаты: '+category)
    if require_mentions:
        for category in before.keys()-after.keys():
            issues.append('В переводе пропущено условие оплаты: '+category)
    return issues


def metro_proximity_claims(text, language='zh', station_names=()):
    """Positive metro proximity, entrance and at-the-door claims, scoped only."""
    result = set()
    names = [re.escape(name) for name in station_names if isinstance(name, str) and len(name.strip()) >= 2]
    for clause in re.split(r'[。；;!?！？\n]+', text):
        if re.search(r'未确认|未被证实|待核实|尚未确认|не\s+подтвержд\w*|неизвест\w*', clause, re.I):
            continue
        if language == 'zh':
            if re.search(r'(?:不|并非|不是)\s*(?:近地铁|地铁口|出门就是地铁)', clause):
                continue
            if re.search(r'(?:近|靠近|毗邻).{0,30}地铁|地铁(?:站)?(?:附近|旁|边|周边)|地铁(?:站)?步行可达', clause):
                result.add('near')
            if not re.search(r'公交|巴士|火车|铁路', clause) and re.search(r'(?:近|靠近|毗邻)[^，。]{1,30}站|站(?:附近|旁|边)', clause):
                result.add('near')
            if re.search(r'地铁口', clause):
                result.update(('near', 'entrance'))
            if re.search(r'出门(?:就)?是.{0,12}地铁', clause):
                result.update(('near', 'at-door'))
            if names and re.search(r'(?:近|靠近|毗邻)\s*(?:'+'|'.join(names)+r')|(?:'+'|'.join(names)+r').{0,40}站(?:附近|旁|边)', clause, re.I):
                result.add('near')
        elif language == 'ru':
            if re.search(r'не\s+(?:рядом|близко)\s+с?\s*метро', clause, re.I):
                continue
            if re.search(r'рядом\s+с\s+(?:станци\w*\s+)?метро|недалеко\s+от\s+метро|близко\s+(?:к|от)\s+метро|у\s+метро', clause, re.I):
                result.add('near')
            if re.search(r'(?:у|возле|рядом\s+с)\s+вход\w*\s+(?:в\s+)?метро', clause, re.I):
                result.update(('near', 'entrance'))
            if re.search(r'метро\s+(?:прямо\s+)?у\s+(?:дома|подъезда)|вышел.{0,12}метро', clause, re.I):
                result.update(('near', 'at-door'))
        else:
            raise ValueError('Неверный язык близости метро')
    return result


def metro_proximity_issues(source, output, fields=None, source_language='zh', output_language='zh'):
    fields = fields or {}
    stations = [fields.get('metro', '')]
    before = metro_proximity_claims(source, source_language, stations)
    after = metro_proximity_claims(output, output_language, stations)
    audit = fields.get('sourceAudit') if isinstance(fields.get('sourceAudit'), dict) else {}
    blocked = 'walk' in audit.get('blockedFields', [])
    walk = fields.get('walk')
    # `walk` is the existing typed minutes-on-foot control. A bus/car duration,
    # zero, an empty control, or conflicted source value does not authorize it.
    if not blocked and type(walk) in (str, int, float):
        literal = str(walk).strip()
        if not re.search(r'автобус|машин|транспорт|公交|巴士|bus|car', literal, re.I):
            match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(?:мин(?:ут\w*)?\.?\s*(?:пешком)?|分钟(?:步行)?)?', literal, re.I)
            if match and 0 < float(match[1].replace(',', '.')) <= 120:
                before.add('near')
    # Explicit scoped walking access can also be supplied by the source prose.
    if not blocked and re.search(r'步行\s*\d+\s*分钟.{0,20}地铁|\d+\s*минут\w*\s*пешком.{0,20}метро', source, re.I):
        before.add('near')
    return ['Не подтверждена близость к метро: '+claim for claim in sorted(after-before)]
