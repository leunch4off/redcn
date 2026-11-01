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
    @staticmethod
    def request_limit(path):
        from workspace_backup import request_limit
        return request_limit(path)

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

    def _save_routes(self, card_id, data, *, actor='Mac owner'):
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
        return language_service._save(self.directory, card_id, row[1], card, actor=actor)

    def dispatch(self, path, data=None, *, namespace='local', query=None, actor=None):
        import language_service
        try:
            import workspace_backup
            document=workspace_backup.dispatch(self.directory,path,data,namespace=namespace)
            if document:return document
            import publication_review
            document=publication_review.dispatch(self.directory,path,data,actor=actor or ('Mac owner' if namespace=='local' else 'paired-phone'))
            if document:return document
            import dictionary_reviews
            document=dictionary_reviews.dispatch(self.directory,path,data,actor=actor or ('Mac owner' if namespace=='local' else 'paired-phone'))
            if document:return document
            import university_route_cache
            document=university_route_cache.dispatch(self.directory,path,data)
            if document:return document
            if re.fullmatch(r'fact-revisions/[a-f0-9]{32}',path) and data is None:
                from fact_ledger import list_fact_revisions
                return 200,{'revisions':list_fact_revisions(self.directory,path.rsplit('/',1)[-1])},'application/json; charset=utf-8',{}
            if path=='import-cache' and data is not None:
                if namespace!='local' or data.get('confirm')!='clear-import-cache':raise ValueError('Подтвердите очистку только кеша в редакторе Mac')
                from fact_ledger import clear_import_cache
                return 200,clear_import_cache(self.directory),'application/json; charset=utf-8',{}
            if path=='table-export' and data is not None:
                from table_export import export_table
                content,mime,name=export_table(data)
                return 200,content,mime,{'Content-Disposition':'attachment; filename="'+name+'"'}
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
                return 200, self._save_routes(match[1], data, actor=actor or ('Mac owner' if namespace=='local' else 'paired-phone')), 'application/json; charset=utf-8', {}
        except language_service.LanguageConflict as error:
            return 409, {'error': str(error)}, 'application/json; charset=utf-8', {}
        except language_service.MissingCard as error:
            return 404, {'error': str(error)}, 'application/json; charset=utf-8', {}
        return None
