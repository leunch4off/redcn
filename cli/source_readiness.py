"""Public, text-free summary of what the browser actually checked."""
import math
from gallery_coverage import normalized_coverage

SECTIONS = ('gallery', 'description', 'characteristics')
LABELS = {'gallery': 'Фотографии', 'description': 'Описание', 'characteristics': 'Характеристики'}
REASONS = ('ready', 'partial', 'not_found', 'not_visited_budget')


def source_readiness(listing):
    capture = listing.get('capture') if isinstance(listing, dict) else None
    if not isinstance(capture, dict):
        old = listing.get('sourceReadiness') if isinstance(listing, dict) else None
        if isinstance(old, dict) and isinstance(old.get('sections'), list):
            capture = old
        else:
            return {'state': 'unknown', 'completed': False, 'partial': False,
                    'sections': [], 'warnings': ['Проверка полноты разделов источника не записана.']}
    rows = {row.get('section', row.get('name', row.get('id'))): row for row in capture.get('sections', [])
            if isinstance(row, dict)} if isinstance(capture.get('sections'), list) else {}
    sections, warnings = [], []
    for name in SECTIONS:
        row = rows.get(name, {})
        reason = row.get('reason')
        visited = row.get('visited') is True or type(row.get('reads')) is int and row['reads'] > 0
        if reason not in REASONS:
            reason = ('ready' if row.get('ready') is True else
                      'not_visited_budget' if not visited else
                      'partial' if row.get('found') is True else 'not_found')
        found = row.get('found') if visited and type(row.get('found')) is bool else None
        sections.append({'name': name, 'reason': reason, 'visited': visited,
                         'found': found, 'ready': reason == 'ready'})
        if reason != 'ready':
            detail = {'not_visited_budget': 'не проверено: закончился лимит времени',
                      'not_found': 'раздел не обнаружен при проверке',
                      'partial': 'полнота раздела не подтверждена'}[reason]
            warnings.append(LABELS[name] + ' — ' + detail + '.')
    album=normalized_coverage(capture.get('galleryCoverage') or capture.get('gallery'))
    album_partial=bool(album and (album['missing'] or album['overLimit']))
    if album_partial:
        warnings.append('Альбом — найдено '+str(album['observed'])+' из '+str(album['expected'] or '?')+' фото; не найдено '+str(album['missing'] or 0)+', сверх лимита '+str(album['overLimit'])+'.')
    complete = all(row['ready'] for row in sections) and capture.get('completed') is not False and not album_partial
    if not complete and not warnings:
        warnings.append('Разделы обнаружены, но итоговая проверка сбора не завершена.')
    result = {'state': 'ready' if complete else 'partial', 'completed': complete,
              'partial': not complete, 'sections': sections, 'warnings': warnings}
    if album:result['gallery']=album
    for key in ('seconds', 'activeSeconds', 'budgetSeconds', 'budgetOverrunSeconds'):
        value = capture.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 7200:
            result[key] = round(value, 3)
    return result
