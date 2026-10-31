"""Authenticated routes for the incoming feed and the temporary import audit.

The desktop/phone HTTP handlers must authenticate before calling dispatch.
The feed never calls the importer: only an explicit UI scan starts that job.
"""
import json
import math
from pathlib import Path
import re
import sqlite3


class WorkspaceAPI:
    def __init__(self, directory, *, browser_channel='chrome'):
        from listing_watch import ListingWatch
        from import_diagnostics import ImportDiagnostics
        self.directory = Path(directory)
        self.diagnostics = ImportDiagnostics(directory)
        self.diagnostics.recover_interrupted()
        self.watch = ListingWatch(directory, browser_channel=browser_channel)

    def close(self):
        self.watch.close()
        self.diagnostics.close()

    def _save_routes(self, card_id, data):
        import language_service
        from university_routes import validate_routes
        if set(data) != {'routes', 'expectedUpdated'}:
            raise ValueError('Нужны маршруты и версия открытой карточки')
        stamp = data.get('expectedUpdated')
        if (type(stamp) not in (float, int) or not 0 <= stamp <= 10_000_000_000_000
                or not math.isfinite(stamp)):
            raise ValueError('Обновите карточку перед сохранением маршрутов')
        rows = validate_routes(data.get('routes'))
        # Match the established card reader's missing-table/hidden-card rules.
        language_service._read(self.directory, card_id)
        with sqlite3.connect(self.directory/'listings.sqlite3', timeout=15) as db:
            row = db.execute('SELECT updated,payload FROM results WHERE id=?', (card_id,)).fetchone()
        if not row:
            raise language_service.MissingCard('Карточка не найдена')
        if row[0] != stamp:
            raise language_service.LanguageConflict('Карточка изменилась. Обновите её перед сохранением маршрутов.')
        card = json.loads(row[1])
        if not isinstance(card, dict) or not isinstance(card.get('fields'), dict) or card['fields'].get('isDemo') is True:
            raise ValueError('Нужна настоящая сохранённая карточка')
        card['fields']['universityRoutes'] = rows
        return language_service._save(self.directory, card_id, row[1], card)

    def dispatch(self, path, data=None, *, namespace='local', query=None):
        import language_service
        try:
            if path == 'photo-ai':
                from photo_ai_settings import load_config, save_config
                value = load_config(self.directory) if data is None else save_config(self.directory, data)
                if data is not None:
                    from photo_ai_runtime import start_background
                    start_background(self.directory)
                return 200, value, 'application/json; charset=utf-8', {}
            document = self.watch.dispatch(path, data, query=query)
            if document:
                return document
            if path == 'import-diagnostics':
                if data is None:
                    value = self.diagnostics.status()
                elif data.get('action') == 'arm':
                    value = self.diagnostics.arm(data.get('target', 10))
                elif data.get('action') == 'pause':
                    value = self.diagnostics.pause()
                else:
                    raise ValueError('Неизвестное действие диагностической записи')
                return 200, value, 'application/json; charset=utf-8', {}
            if path == 'import-diagnostics/tg.zip' and data is None:
                content, mime, name = self.diagnostics.bundle_asset()
                return 200, content, mime, {'Content-Disposition': 'attachment; filename="tg.zip"'}
            match = re.fullmatch(r'cards/([a-f0-9]{32})/routes', path)
            if match and data is not None:
                return 200, self._save_routes(match[1], data), 'application/json; charset=utf-8', {}
        except language_service.LanguageConflict as error:
            return 409, {'error': str(error)}, 'application/json; charset=utf-8', {}
        except language_service.MissingCard as error:
            return 404, {'error': str(error)}, 'application/json; charset=utf-8', {}
        return None
