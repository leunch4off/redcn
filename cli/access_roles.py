"""Explicit capabilities for paired devices, before any phone route dispatch.

Pairing is a workspace invitation, not a public user-registration API. These
roles do not claim separate per-user tenants or ownership of arbitrary files.
The desktop token remains the only authority that can manage device grants.
"""
import json
from pathlib import Path
import re
import sqlite3

ROLES = frozenset(('owner', 'editor', 'viewer'))
_CARD = r'[a-f0-9]{32}'
_ASSET = r'[a-f0-9]{32,64}'
_MEDIA = r'[a-f0-9]{64}'
_VIEW_GET = (r'state', r'cards', rf'cards/{_CARD}(?:\.zip)?',
             rf'(?:media|previews)/{_MEDIA}', rf'card-exports/{_CARD}', rf'exports/{_ASSET}')
_EDITOR_GET = (r'learning', r'language', r'language/catalog/(?:title|description|tags)',
               r'universities', r'city-pois', r'listing-watch', r'listing-watch/items',
               r'listing-watch/notifications',r'activity-log',rf'fact-revisions/{_CARD}',rf'jobs/{_CARD}',
               r'import-diagnostics', r'photo-ai', r'design', rf'design-images/{_CARD}',
               rf'dossier-(?:options|design)/{_CARD}', r'photo-duplicates',
               r'dictionary-reviews',rf'publication-review/{_CARD}')
_EDITOR_POST = (r'learning', r'logout', r'import', rf'cards/{_CARD}/(?:prepare|analyze|routes|rewrite|edit-text)',
                r'university-route-cache/(?:lookup|remember)',
                r'language/search', r'universities/search', r'city-pois/search', r'translate',
                r'listing-watch/read', r'card-export', r'listing-map',r'design',r'design-images',
                rf'dossier-client/{_CARD}', r'dictionary-reviews',r'table-export',rf'publication-review/{_CARD}')
_OWNER_GET = (r'import-diagnostics/tg\.zip', rf'dossier-backup/{_CARD}',rf'card-archive/{_CARD}',rf'card-attachments/{_CARD}/{_ASSET}')
_OWNER_POST = (r'dossier-import', r'photo-ai',rf'card-archive/{_CARD}',
               r'import-diagnostics', r'listing-watch', r'listing-watch/check')


class AccessRoleConflict(ValueError):
    pass


def capabilities(role):
    known = isinstance(role,str) and role in ROLES
    edit = role in ('owner', 'editor')
    owner = role == 'owner'
    return {'readCards': known, 'editCards': edit, 'importListings': edit,
            'readDossier': owner, 'manageDossier': owner,
            'readDesign': edit, 'manageDesign': edit,
            'manageSettings': owner, 'manageDiagnostics': owner,
            'readDiagnostics': edit, 'downloadDiagnostics': owner,
            'manageDictionary': edit, 'proposeDictionary': edit,
            'manageAccess': False}


def phone_route_allowed(role, method, path, data=None):
    """Fail closed for new routes. Session management is desktop-only."""
    if not isinstance(role,str) or role not in ROLES or method not in ('GET', 'POST') or not isinstance(path, str):
        return False
    if not re.fullmatch(r'[a-zA-Z0-9_./-]+', path) or '..' in path or path.startswith('/'):
        return False
    # Whole-workspace export and archive inspection require the desktop token,
    # including when a paired phone carries the owner role.
    if path.startswith('workspace-backup'):return False
    if path=='learning' and method=='POST' and isinstance(data,dict) and data.get('action')=='scheduler-config' and role!='owner':
        return False
    if method == 'GET':
        patterns = _VIEW_GET + (_EDITOR_GET if role in ('owner', 'editor') else ()) + (_OWNER_GET if role == 'owner' else ())
    else:
        patterns = (r'logout',) + (_EDITOR_POST if role in ('owner', 'editor') else ()) + (_OWNER_POST if role == 'owner' else ())
    return any(re.fullmatch(pattern, path) for pattern in patterns)


def dispatch_sessions(pairing, data=None):
    """Desktop handler must verify its existing local owner token first."""
    try:
        if data is None:
            result = pairing.list_sessions()
        elif not isinstance(data, dict):
            raise ValueError('Нужен объект изменения прав устройства')
        elif data.get('action') == 'set-role':
            if set(data) - {'action', 'sessionId', 'role', 'label', 'expectedRevision'}:
                raise ValueError('Неизвестные поля изменения прав')
            result = pairing.manage_session(data.get('sessionId'), role=data.get('role'),
                label=data.get('label'), expected_revision=data.get('expectedRevision'))
        elif data.get('action') == 'revoke':
            if set(data) != {'action', 'sessionId', 'expectedRevision'}:
                raise ValueError('Нужны ID сессии и её актуальная версия')
            result = pairing.manage_session(data.get('sessionId'), revoke=True,
                expected_revision=data.get('expectedRevision'))
        else:
            raise ValueError('Неизвестное действие с правами устройства')
        return 200, result, 'application/json; charset=utf-8', {}
    except AccessRoleConflict as error:
        return 409, {'error': str(error)}, 'application/json; charset=utf-8', {}
    except ValueError as error:
        return 400, {'error': str(error)}, 'application/json; charset=utf-8', {}


def viewer_card(card):
    """Client-facing fields only; new operator metadata cannot leak by default."""
    allowed_fields = {'price', 'pricePeriod', 'priceCurrency', 'deposit', 'depositCurrency',
        'area', 'rooms', 'bedrooms', 'livingRooms', 'living', 'kitchen', 'floor', 'floors',
        'metro', 'walk', 'district', 'complex', 'city', 'address', 'latitude', 'longitude',
        'photos', 'features', 'featureCounts', 'layout', 'utilities', 'registration',
        'registrationMonths', 'registrationPeople', 'pickup', 'arrival', 'support', 'luggage',
        'university', 'campus', 'uniTime', 'uniMode', 'route', 'universities', 'universityRoutes'}
    result = {key: card[key] for key in ('id', 'updated', 'titles', 'body', 'tags',
        'photoCount', 'media', 'publicationPolicyVersion') if key in card}
    fields = card.get('fields') if isinstance(card.get('fields'), dict) else {}
    result['fields'] = {key: value for key, value in fields.items() if key in allowed_fields}
    # Detach from the original card; callers may add view-only labels afterwards.
    return json.loads(json.dumps(result, ensure_ascii=False))


def _read_cards(directory):
    path = Path(directory) / 'listings.sqlite3'
    if not path.is_file() or path.is_symlink():
        return []
    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=10) as db:
        try:
            rows = db.execute('SELECT id,payload FROM results').fetchall()
        except sqlite3.OperationalError:
            return []
    output = []
    from client_publication import client_card,PublicationReviewRequired
    for card_id, payload in rows:
        try:
            card = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if isinstance(card, dict) and isinstance(card.get('fields'), dict) and card['fields'].get('isDemo') is not True:
            try:client_card(card)
            except (PublicationReviewRequired,ValueError,TypeError):continue
            output.append((card_id, card))
    return output


def viewer_asset_allowed(directory, kind, asset_id):
    """Viewer media must belong to a visible card; dossiers are never admitted."""
    cards = _read_cards(directory)
    if kind == 'export':
        if not re.fullmatch(_ASSET, str(asset_id)):
            return False
        path = Path(directory) / 'card-assets' / 'dossiers.sqlite3'
        if not path.is_file() or path.is_symlink():
            return False
        from client_publication import PUBLICATION_POLICY_VERSION
        with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=10) as db:
            try:
                row = db.execute('SELECT e.card_id,e.digest,p.version,p.digest FROM exports e JOIN export_publication_policy p ON p.export_id=e.id WHERE e.id=?', (asset_id,)).fetchone()
            except sqlite3.OperationalError:
                return False
        return bool(row and row[0] in {card_id for card_id, _ in cards}
                    and row[2] == PUBLICATION_POLICY_VERSION and row[1] == row[3])
    if kind != 'media' or not re.fullmatch(_MEDIA, str(asset_id)):
        return False
    path = Path(directory) / 'media.sqlite3'
    if not path.is_file() or path.is_symlink():
        return False
    from phone_relay import source_images
    urls = {url for _, card in cards for url in source_images(card)}
    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=10) as db:
        try:
            originals = {digest for url, digest in db.execute('SELECT url,id FROM photo_urls') if url in urls}
            if asset_id in originals:
                return True
            row = db.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()
        except sqlite3.OperationalError:
            return False
    if not row:
        return False
    try:
        metadata = json.loads(row[0])
    except (ValueError, TypeError):
        return False
    return (metadata.get('kind') == 'crop' and metadata.get('originalId') in originals
            and metadata.get('verification') in ('manual', 'ocr-checked'))
