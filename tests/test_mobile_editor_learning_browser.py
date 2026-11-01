"""393px editor/dictionary focus acceptance; reduced viewport emulates space,
not a claim that a physical iOS keyboard was opened by desktop Playwright.
"""
import threading
import unittest

import test_phone_browser as fixture


class MobileEditorLearningBrowserTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        fixture.PhoneBrowserAcceptance.setUp(self)
        self.desktop_thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        self.desktop_thread.start()
        self.desktop = 'http://127.0.0.1:'+str(self.controller.server_port)

    def tearDown(self):
        self.controller.shutdown()
        self.desktop_thread.join()
        fixture.PhoneBrowserAcceptance.tearDown(self)

    def focus_in_visual_viewport(self, locator):
        locator.evaluate("node=>{for(let parent=node.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;}")
        locator.scroll_into_view_if_needed()
        locator.focus()
        locator.evaluate("node=>node.scrollIntoView({block:node.getBoundingClientRect().height>(visualViewport?.height||innerHeight)-80?'start':'center',inline:'nearest',behavior:'instant'})")
        self.page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        state = locator.evaluate('''node=>{const r=node.getBoundingClientRect(),v=visualViewport;return {
            focused:document.activeElement===node,left:r.left,right:r.right,top:r.top,bottom:r.bottom,
            viewportLeft:v?.offsetLeft||0,viewportTop:v?.offsetTop||0,viewportWidth:v?.width||innerWidth,
            viewportHeight:v?.height||innerHeight,fontSize:parseFloat(getComputedStyle(node).fontSize),
            rootWidth:document.documentElement.scrollWidth};}''')
        self.assertTrue(state['focused'])
        self.assertGreaterEqual(state['left'], state['viewportLeft']-1)
        self.assertLessEqual(state['right'], state['viewportLeft']+state['viewportWidth']+1)
        self.assertGreaterEqual(state['top'], state['viewportTop']-1)
        # Large multiline editors may exceed the available height; their top and
        # insertion area must remain reachable instead of requiring horizontal pan.
        self.assertLess(state['top'], state['viewportTop']+state['viewportHeight']-40)
        self.assertGreaterEqual(state['fontSize'], 16)
        self.assertLessEqual(state['rootWidth'], 393)

    def open_desktop(self):
        self.page.goto(self.desktop, wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")

    def test_editor_and_dictionary_inputs_remain_reachable_in_full_and_reduced_visual_viewports(self):
        self.open_desktop()
        self.page.locator('#source').fill('Квартира 54 м², цена 80000 руб. Есть холодильник и рабочий стол.')
        self.page.locator('#extract').click()
        self.page.locator('#generate').click()
        for height in (852, 500):
            self.page.set_viewport_size({'width': 393, 'height': height})
            for selector in ['#url', '#metro', '#area', '#price', '#description', '#outputTitle', '#output', '#tags']:
                self.focus_in_visual_viewport(self.page.locator(selector))
        self.page.locator('nav [data-tab="rules"]').click()
        self.page.locator('#dictionarySearch').fill('стол')
        self.page.locator('#dictionaryResults .language-result').first.wait_for()
        for height in (852, 500):
            self.page.set_viewport_size({'width': 393, 'height': height})
            self.focus_in_visual_viewport(self.page.locator('#dictionarySearch'))
            self.focus_in_visual_viewport(self.page.locator('#dictionaryScope'))
            self.focus_in_visual_viewport(self.page.locator('#trigger'))
            self.focus_in_visual_viewport(self.page.locator('#translation'))
        self.assertLessEqual(self.page.locator('#dictionaryResults .language-result').count(), 40)

    def test_phone_learning_translation_and_manual_statistics_controls_fit_and_respect_owner_settings(self):
        fixture.PhoneBrowserAcceptance.connect(self)
        self.page.locator('#phoneTabs [data-phone-view="setup"]').click()
        panel = self.page.locator('#phoneLearningMount .learning-studio')
        panel.locator('summary').click()
        panel.get_by_text('На проверке:', exact=False).first.wait_for()
        self.assertTrue(panel.get_by_label('Обучать адаптер во время простоя Mac', exact=True).is_hidden())
        for height in (852, 500):
            self.page.set_viewport_size({'width': 393, 'height': height})
            for label in ['Направление перевода', 'Ваш исходный текст', 'Перевод · проверьте и при необходимости исправьте', 'Русский текст', 'Китайский текст']:
                self.focus_in_visual_viewport(panel.get_by_label(label, exact=True))
            panel.get_by_label('Что запомнить', exact=True).select_option('tag_stat')
            for label in ['Хештег', 'Просмотры · ваша приблизительная оценка', 'Дата наблюдения', 'Контекст статистики · например, теги квартиры у метро']:
                self.focus_in_visual_viewport(panel.get_by_label(label, exact=True))
            panel.get_by_label('Что запомнить', exact=True).select_option('dictionary')
        self.page.locator('#phoneTabs [data-phone-view="cards"]').click()
        fixture.grant_paired_role(self, 'owner')
        self.page.locator('#phoneTabs [data-phone-view="setup"]').click()
        panel.locator('button').filter(has_text='Обновить').first.click()
        panel.get_by_label('Обучать адаптер во время простоя Mac', exact=True).wait_for(state='visible')
        fixture.PhoneBrowserAcceptance.assert_mobile_fit(self)


if __name__ == '__main__':
    unittest.main()
