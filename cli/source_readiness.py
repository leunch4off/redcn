"""Public, text-free summary of what the browser actually checked."""
import math
from gallery_coverage import normalized_coverage

SECTIONS = ('gallery', 'description', 'characteristics')
LABELS = {'gallery': 'Фотографии', 'description': 'Описание', 'characteristics': 'Характеристики'}
REASONS = ('ready', 'partial', 'not_found', 'not_visited_budget')


def _schema_summary(listing, result):
    from source_schema_health import public_summary
    old=listing.get('sourceReadiness') if isinstance(listing,dict) else None
    raw=listing.get('sourceSchemaHealth') if isinstance(listing,dict) else None
    if raw is None and isinstance(old,dict):raw=old.get('schema')
    schema=public_summary(raw)
    if schema is None:return result
    result={**result,'schema':schema,'warnings':list(result['warnings'])}
    if schema['state'] in ('partial','unsupported'):
        result.update(state='partial',completed=False,partial=True)
        result['warnings'].extend(schema['warnings'])
    return result


def source_readiness(listing):
    capture = listing.get('capture') if isinstance(listing, dict) else None
    if not isinstance(capture, dict):
        old = listing.get('sourceReadiness') if isinstance(listing, dict) else None
        if isinstance(old, dict) and isinstance(old.get('sections'), list):
            capture = old
        else:
            return _schema_summary(listing, {'state': 'unknown', 'completed': False, 'partial': False,
                    'sections': [], 'warnings': ['Проверка полноты разделов источника не записана.']})
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
    album_partial=bool(album and (album['missing'] or album['overLimit'] or album.get('previewOnly')))
    album_unknown=bool(album and album['state']=='unknown')
    if album_partial:
        warnings.append('Альбом — найдено '+str(album['observed'])+' из '+str(album['expected'] or '?')+' фото; не найдено '+str(album['missing'] or 0)+', сверх лимита '+str(album['overLimit'])+'.')
    if album and album.get('previewOnly'):
        warnings.append('У '+str(album['previewOnly'])+' фото пока найдены только миниатюры; ссылки на основные версии ещё не получены.')
    if album_unknown:warnings.append('Полнота альбома не подтверждена: общий счётчик или ссылки на основные версии неизвестны либо противоречивы.')
    sections_ready=all(row['ready'] for row in sections)
    complete = sections_ready and capture.get('completed') is not False and not album_partial and not album_unknown
    if not complete and not warnings:
        warnings.append('Разделы обнаружены, но итоговая проверка сбора не завершена.')
    unknown=album_unknown and sections_ready and not album_partial
    result = {'state': 'ready' if complete else 'unknown' if unknown else 'partial', 'completed': complete,
              'partial': not complete and not unknown, 'sections': sections, 'warnings': warnings}
    if album:result['gallery']=album
    for key in ('seconds', 'activeSeconds', 'budgetSeconds', 'budgetOverrunSeconds'):
        value = capture.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 7200:
            result[key] = round(value, 3)
    return _schema_summary(listing, result)
