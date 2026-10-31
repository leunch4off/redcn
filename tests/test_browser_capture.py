"""Lazy capture acceptance: bounded scrolling, evolving gallery and owned tabs."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import browser_import

URL = 'https://www.cian.ru/rent/flat/300691182/'
TEXT = '2-комнатная квартира, 54 м². Аренда 80 000 ₽. Отдельные комнаты. Есть рабочий стол и кухня.'


def snapshot(images=(), text=TEXT):
    return {'text': text, 'fallback': text, 'body': text,
            'title': 'Аренда квартиры', 'metadata': '', 'images': list(images)}


class ScrollPage:
    def __init__(self, stages, positions):
        self.url = URL
        self.stages = list(stages)
        self.positions = list(positions)
        self.stage = -1
        self.expressions = []
        self.waits = []

    def is_closed(self):
        return False

    def evaluate(self, expression):
        self.expressions.append(expression)
        if expression == browser_import.SCROLL:
            self.stage = min(self.stage+1, len(self.stages)-1)
            return self.positions[min(self.stage, len(self.positions)-1)]
        if expression == browser_import.SCROLL_TOP:
            return None
        return self.stages[max(0, self.stage)]

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


class CaptureTests(unittest.TestCase):
    def test_collects_virtualized_gallery_and_new_facts_until_stable_bottom(self):
        original = snapshot(['https://images.cdn-cian.ru/first.jpg'])
        final = snapshot(['https://images.cdn-cian.ru/later.jpg'], TEXT+' Есть пианино.')
        page = ScrollPage([final]*4, [{'height': 2000, 'top': 700, 'bottom': False},
                                     {'height': 2000, 'top': 1300, 'bottom': True},
                                     {'height': 2000, 'top': 1300, 'bottom': True},
                                     {'height': 2000, 'top': 1300, 'bottom': True}])
        validate = MagicMock()
        result, audit = browser_import.capture_scrolled(page, original, time.monotonic()+20, validate)
        self.assertIn('пианино', result['text'])
        self.assertEqual(result['images'], original['images']+final['images'])
        self.assertTrue(audit['completed'])
        self.assertEqual(audit['scrolls'], 4)
        self.assertEqual(page.expressions[-1], browser_import.SCROLL_TOP)
        self.assertEqual(validate.call_count, 4)

    def test_infinite_recommendation_scroll_is_bounded_and_reported(self):
        page = ScrollPage([snapshot()], [{'height': 100000, 'top': 700, 'bottom': False}])
        result, audit = browser_import.capture_scrolled(page, snapshot(), time.monotonic()+20,
                                                        lambda *_args, **_kwargs: None, max_scrolls=3)
        self.assertFalse(audit['completed'])
        self.assertTrue(audit['bounded'])
        self.assertEqual(audit['scrolls'], 3)
        self.assertEqual(result['text'], TEXT)

    def test_cancel_keeps_original_and_restores_scroll(self):
        page = ScrollPage([snapshot()], [{}])
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(ValueError, 'отменён'):
            browser_import.capture_scrolled(page, snapshot(), time.monotonic()+20,
                                            lambda *_args, **_kwargs: None, cancel=event)
        self.assertEqual(page.expressions, [browser_import.SCROLL_TOP])

    def test_redirect_is_validated_before_reading_lazy_content(self):
        page = ScrollPage([snapshot()], [{}])
        page.url = 'https://unrelated.invalid/'
        def reject(*_args, **_kwargs):
            raise ValueError('Неверный сайт')
        with self.assertRaisesRegex(ValueError, 'Неверный сайт'):
            browser_import.capture_scrolled(page, snapshot(), time.monotonic()+20, reject)
        self.assertNotIn(browser_import.SNAPSHOT, page.expressions)

    def test_uses_new_owned_tab_and_rejects_untrusted_photo_candidates(self):
        existing, page, context, pw, api = [MagicMock() for _ in range(5)]
        page.url = URL
        page.is_closed.return_value = False
        snap = snapshot(['/full.jpg', 'https://bad.invalid/x.jpg'])
        page.evaluate.side_effect = lambda script, *_args: {'height': 800, 'bottom': True} if script == browser_import.SCROLL else snap
        context.pages = [existing]
        context.new_page.return_value = page
        pw.chromium.launch_persistent_context.return_value = context
        api.sync_playwright.return_value.__enter__.return_value = pw
        api.Error = type('FakeBrowserError', (Exception,), {})
        api.TimeoutError = type('FakeBrowserTimeout', (api.Error,), {})
        def validate(url, **kwargs):
            if 'bad.invalid' in url:
                raise ValueError('Неверный сайт')
            return url
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'playwright': MagicMock(), 'playwright.sync_api': api}):
            result = browser_import.load_browser(URL, Path(folder),
                lambda markup, url: {'url': url, 'images': []}, validate)
        existing.goto.assert_not_called()
        context.new_page.assert_called_once()
        context.close.assert_called_once()
        self.assertEqual(result['images'], ['https://www.cian.ru/full.jpg'])
        self.assertTrue(result['capture']['completed'])

    def test_check_appearing_after_scroll_waits_for_manual_completion(self):
        page = ScrollPage([snapshot()], [{'height': 800, 'bottom': True}])
        challenge = snapshot(text='Подтвердите, что вы не робот')
        evaluations = iter([challenge, snapshot(), snapshot(), snapshot()])
        base_evaluate = page.evaluate
        def evaluate(expression):
            if expression == browser_import.SNAPSHOT:
                return next(evaluations)
            return base_evaluate(expression)
        page.evaluate = evaluate
        reports = []
        result, audit = browser_import.capture_scrolled(page, snapshot(), time.monotonic()+20,
            lambda *_args, **_kwargs: None, status=reports.append)
        self.assertEqual(result['text'], TEXT)
        self.assertTrue(audit['completed'])
        self.assertTrue(any('вручную' in report for report in reports))
        self.assertIn(500, page.waits)


if __name__ == '__main__':
    unittest.main()
