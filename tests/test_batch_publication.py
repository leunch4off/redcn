"""Offline batch intake: publication checks do not consume pending candidates."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import redcn


class BatchPublicationTests(unittest.TestCase):
    url = 'https://www.avito.ru/moskva/kvartiry/test_1000000000'

    def run_batch(self, directory, listing, data=None):
        batch = Path(directory) / 'batch.json'
        batch.write_text(json.dumps([{'url': self.url, 'seller': 'owner',
                                      'data': data or {}}]), encoding='utf-8')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(redcn, 'load_listing', return_value=listing) as loader, \
             patch.object(redcn, 'generate', return_value={
                 'titles': ['测试'], 'body': '测试', 'tags': '#莫斯科'}) as generator, \
             patch.object(redcn, 'download_photos', return_value=[]) as photos:
            redcn.main(['--batch', str(batch), '--cache-dir', directory,
                        '--download-photos', str(Path(directory) / 'photos'), '--json'])
        return json.loads(output.getvalue()), loader, generator, photos

    def seen(self, directory):
        with sqlite3.connect(Path(directory) / 'listings.sqlite3') as connection:
            return connection.execute('SELECT COUNT(*) FROM seen').fetchone()[0]

    def test_explicit_archived_removed_and_unavailable_never_become_new(self):
        for status in ('archived', 'removed', 'unavailable'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                listing = {'text': 'Старый текст и цена', 'images': [], 'fields': {
                    'publicationStatus': status, 'eligibleForNew': False}}
                rows, _, generator, photos = self.run_batch(directory, listing,
                    {'publicationStatus': 'active', 'eligibleForNew': True})
                self.assertEqual(rows[0]['status'], 'filtered')
                self.assertEqual(rows[0]['publicationStatus'], status)
                self.assertIn('исключено', rows[0]['reason'])
                generator.assert_not_called()
                photos.assert_not_called()
                self.assertEqual(self.seen(directory), 0)

    def test_unknown_or_missing_avito_state_waits_and_can_become_new_later(self):
        for metadata in ({}, {'publicationStatus': 'unknown', 'eligibleForNew': False}):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as directory:
                listing = {'text': 'Неполная страница', 'images': [], 'fields': metadata}
                rows, _, generator, photos = self.run_batch(directory, listing)
                self.assertEqual(rows[0]['status'], 'pending')
                self.assertEqual(rows[0]['publicationStatus'], 'unknown')
                generator.assert_not_called()
                photos.assert_not_called()
                self.assertEqual(self.seen(directory), 0)
                active = {'text': 'Проверенная страница', 'images': [], 'fields': {
                    'publicationStatus': 'active', 'eligibleForNew': True}}
                rows, _, generator, photos = self.run_batch(directory, active)
                self.assertEqual(rows[0]['status'], 'new')
                self.assertEqual(rows[0]['publicationStatus'], 'active')
                generator.assert_called_once()
                photos.assert_called_once()
                self.assertEqual(self.seen(directory), 1)
                rows, loader, generator, photos = self.run_batch(directory, active)
                self.assertEqual(rows[0]['status'], 'seen')
                loader.assert_not_called()
                generator.assert_not_called()

    def test_conflicting_source_status_is_not_overridden_by_active_holder(self):
        listing = {'publicationStatus': 'active', 'eligibleForNew': True,
                   'fields': {'publicationStatus': 'removed'}}
        decision = redcn.batch_publication_decision(listing, self.url)
        self.assertEqual(decision['status'], 'filtered')
        self.assertEqual(decision['publicationStatus'], 'removed')
        self.assertEqual(redcn.batch_publication_decision({
            'publicationStatus': 'active', 'eligibleForNew': False}, self.url)['status'],
            'pending')

    def test_legacy_cian_flow_remains_compatible_without_claiming_verified_activity(self):
        decision = redcn.batch_publication_decision({}, 'https://www.cian.ru/rent/flat/1/')
        self.assertEqual(decision, {'status': 'new', 'publicationStatus': 'unknown'})
        decision = redcn.batch_publication_decision({'fields': {
            'publicationStatus': 'archived'}}, 'https://www.cian.ru/rent/flat/1/')
        self.assertEqual(decision['status'], 'filtered')


if __name__ == '__main__':
    unittest.main()
