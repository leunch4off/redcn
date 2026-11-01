"""Create/save, dictionary replacement, undo and saved-card restore in Chrome."""
import json
import test_twenty_editor_browser as fixture


class RequiredEditorRegression(fixture.TwentyEditorBrowserAcceptance):
    def test_create_save_replace_and_restore(self):
        self.seed()
        self.page.locator('#captureSnapshots').uncheck()
        self.page.locator('#name').fill('CI fixture apartment')
        self.page.locator('#save').click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('most-objects')||'[]').length===1")
        original = self.page.locator('#output').input_value()
        self.page.locator('#output').evaluate("el=>{const start=el.value.indexOf('冰箱');el.setSelectionRange(start,start+2);el.dispatchEvent(new Event('select'));}")
        self.page.locator('#wordReplacement').fill('冷藏冰箱')
        self.page.locator('#wordApply').click()
        self.assertIn('冷藏冰箱', self.page.locator('#output').input_value())
        self.page.locator('#wordUndo').click()
        self.assertEqual(self.page.locator('#output').input_value(), original)
        self.page.locator('#newObject').click()
        self.assertEqual(self.page.locator('#output').input_value(), '')
        self.page.locator('[data-tab="library"]').click()
        self.page.locator('#list').get_by_role('button', name='Открыть', exact=True).click()
        self.assertEqual(self.page.locator('#output').input_value(), original)
        self.assertEqual(self.page.locator('#name').input_value(), 'CI fixture apartment')
        self.assertEqual(self.page.locator('#price').input_value(), '80000')
        stored = self.page.evaluate("JSON.parse(localStorage.getItem('most-objects'))")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['price'], '80000')
