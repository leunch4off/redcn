"""Descriptive, matched publication statistics. Post views are not tag effects."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import math
import re
import statistics
from urllib.parse import urlsplit, urlunsplit

MAX_POSTS = 500


def _date(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Нужна дата наблюдения YYYY-MM-DD или время с часовым поясом')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        precision = 'date' if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else 'timestamp'
        if precision == 'timestamp' and parsed.tzinfo is None:
            raise ValueError()
        parsed = parsed.replace(tzinfo=timezone.utc) if precision == 'date' else parsed.astimezone(timezone.utc)
        if parsed > datetime.now(timezone.utc)+timedelta(days=1):
            raise ValueError()
        return (parsed.date().isoformat() if precision == 'date' else parsed.isoformat()), parsed, precision
    except (ValueError, OverflowError) as error:
        raise ValueError('Неверная или будущая дата наблюдения') from error


def _number(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError('Нужны положительные числовые площадь и месячная аренда')
    return float(value)


def _tag(value):
    if not isinstance(value, str) or not re.fullmatch(r'#?[\w\u3400-\u9fff-]{1,80}', value):
        raise ValueError('Неверный хештег')
    return '#'+value.lstrip('#')


def normalize_post(value):
    required = {'postId', 'observedAt', 'views', 'tags', 'facts'}
    if not isinstance(value, dict) or not required <= set(value) <= required|{'publishedAt', 'approximate'}:
        raise ValueError('Нужны ID публикации, дата, просмотры, теги и сопоставимые характеристики')
    identity = value['postId']
    if not isinstance(identity, str) or not 1 <= len(identity) <= 500 or any(ord(c) < 32 for c in identity):
        raise ValueError('Нужен ID публикации')
    if re.fullmatch(r'sha256:[a-f0-9]{64}', identity):
        post_id = identity
    else:
        parsed = urlsplit(identity)
        if parsed.scheme:
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Ссылка публикации должна быть HTTPS без логина и пароля')
            identity = urlunsplit(('https', parsed.netloc.lower(), parsed.path, '', ''))
        post_id = 'sha256:'+hashlib.sha256(identity.encode()).hexdigest()
    views = value['views']
    if type(views) is not int or not 0 <= views <= 10_000_000_000:
        raise ValueError('Просмотры — неотрицательное целое число')
    tags = value['tags']
    if not isinstance(tags, list) or not 1 <= len(tags) <= 30:
        raise ValueError('Нужно от 1 до 30 тегов реальной публикации')
    tags = list(dict.fromkeys(_tag(tag) for tag in tags))
    facts = value['facts']
    if not isinstance(facts, dict) or set(facts) != {'metro', 'rooms', 'area', 'rent'}:
        raise ValueError('Для сравнения нужны метро, число комнат, площадь и аренда')
    metro = facts['metro']
    if not isinstance(metro, str) or not metro.strip() or len(metro) > 120 or re.search(r'[<>\x00-\x1f]|https?://', metro):
        raise ValueError('Нужно название станции без адреса, ссылки или HTML')
    if type(facts['rooms']) is not int or not 0 <= facts['rooms'] <= 10:
        raise ValueError('Число комнат — целое число, 0 означает студию')
    observed, observed_time, _ = _date(value['observedAt'])
    published = None
    if value.get('publishedAt'):
        published, published_time, _ = _date(value['publishedAt'])
        if published_time > observed_time:
            raise ValueError('Наблюдение не может предшествовать публикации')
    approximate = value.get('approximate', True)
    if type(approximate) is not bool:
        raise ValueError('Отметьте, приблизительны ли просмотры')
    return {'postId': post_id, 'observedAt': observed, 'publishedAt': published,
            'views': views, 'tags': tags, 'approximate': approximate,
            'facts': {'metro': ' '.join(metro.casefold().replace('ё', 'е').split()),
                      'rooms': facts['rooms'], 'area': _number(facts['area'], 10000),
                      'rent': _number(facts['rent'], 1_000_000_000)}}


def compare_posts(values, eligible_tags):
    if not isinstance(values, list) or len(values) > MAX_POSTS:
        raise ValueError('Допускается до 500 наблюдений публикаций за один анализ')
    if not isinstance(eligible_tags, list) or len(eligible_tags) > 100:
        raise ValueError('Сначала передайте до 100 тегов, подходящих по фактам квартиры')
    eligible = list(dict.fromkeys(_tag(tag) for tag in eligible_tags))
    latest = {}
    for raw in values:
        row = normalize_post(raw)
        previous = latest.get(row['postId'])
        if previous is None or row['observedAt'] > previous['observedAt']:
            latest[row['postId']] = row
        elif row['observedAt'] == previous['observedAt'] and row != previous:
            raise ValueError('Одна публикация с одной датой имеет противоречивые наблюдения')
    rows = sorted(latest.values(), key=lambda row: row['postId'])
    results = []
    for tag in eligible:
        treated = [row for row in rows if tag in row['tags']]
        controls = [row for row in rows if tag not in row['tags']]
        used, pairs = set(), []
        for treatment in treated:
            candidates = []
            for control in controls:
                if control['postId'] in used:
                    continue
                left, right = treatment['facts'], control['facts']
                if (left['metro'], left['rooms']) != (right['metro'], right['rooms']):
                    continue
                area_delta = abs(left['area']-right['area'])/max(left['area'], right['area'])
                rent_delta = abs(left['rent']-right['rent'])/max(left['rent'], right['rent'])
                days = abs((_date(treatment['observedAt'])[1]-_date(control['observedAt'])[1]).total_seconds())/86400
                if area_delta > .10 or rent_delta > .15 or days > 30:
                    continue
                exposure_matched = False
                exposure_difference = None
                if treatment['publishedAt'] and control['publishedAt']:
                    ages = [(_date(row['observedAt'])[1]-_date(row['publishedAt'])[1]).total_seconds()/3600
                            for row in (treatment, control)]
                    exposure_difference = abs(ages[0]-ages[1])
                    if exposure_difference > max(6, max(ages)*.20):
                        continue
                    exposure_matched = all(_date(row['observedAt'])[2] == _date(row['publishedAt'])[2] == 'timestamp'
                                           for row in (treatment, control))
                candidates.append((area_delta+rent_delta+days/300, control['postId'], control, exposure_matched, exposure_difference))
            if not candidates:
                continue
            _, _, control, exposure_matched, exposure_difference = min(candidates)
            used.add(control['postId'])
            pairs.append({'post': treatment['postId'], 'control': control['postId'],
                          'postViews': treatment['views'], 'controlViews': control['views'],
                          'viewDifference': treatment['views']-control['views'],
                          'approximate': treatment['approximate'] or control['approximate'],
                          'exposureMatched': exposure_matched, 'exposureDifferenceHours': exposure_difference})
        results.append({'tag': tag, 'eligibleByFacts': True, 'postsWithTag': len(treated),
                        'matchedPairs': len(pairs), 'pairs': pairs,
                        'medianPairedViewDifference': statistics.median(row['viewDifference'] for row in pairs) if pairs else None,
                        'approximatePairs': sum(row['approximate'] for row in pairs),
                        'exposureUnmatchedPairs': sum(not row['exposureMatched'] for row in pairs),
                        'smallSample': len(pairs) < 5, 'causalEffectKnown': False})
    return {'version': 1, 'state': 'observed-comparison' if any(row['matchedPairs'] for row in results) else 'needs-comparable-observations',
            'inputObservations': len(values), 'uniquePosts': len(rows),
            'repeatedObservationsExcluded': len(values)-len(rows), 'tags': results,
            'postViewsAreTagPopularity': False, 'automaticPromotion': False,
            'method': {'matchingUsesViews': False, 'sameMetroAndRooms': True,
                       'maxRelativeAreaDifference': .10, 'maxRelativeRentDifference': .15,
                       'maxObservationDateDifferenceDays': 30, 'oneControlPerTagPair': True},
            'limitations': ['Просмотры относятся ко всему посту, а не к отдельному тегу.',
                           'Сходные квартиры и даты не устраняют различия фото, автора и аудитории.',
                           'Малая выборка, приблизительные значения и неизвестная экспозиция помечаются отдельно.']}
