"""Desktop language tools: lazy lookup, explicit text replacement and bounded UI."""
import threading
import unittest

import test_phone_browser as acceptance


class LanguageStudioAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        acceptance.PhoneBrowserAcceptance.setUp(self)
        self.desktop_thread=threading.Thread(target=self.controller.serve_forever,daemon=True)
        self.desktop_thread.start()
        self.desktop='http://127.0.0.1:'+str(self.controller.server_port)

    def tearDown(self):
        self.controller.shutdown();self.desktop_thread.join()
        acceptance.PhoneBrowserAcceptance.tearDown(self)

    def open_editor(self):
        self.page.goto(self.desktop,wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
        self.page.locator('#languageComposer').wait_for(state='attached')

    def test_dictionary_loads_on_demand_and_paginates_without_large_dom(self):
        requests=[]
        self.page.on('request',lambda request:requests.append(request.url))
        self.page.route('**/api/local/language',lambda route:route.fulfill(json={
            'dictionary':{'ruZhPairs':89171,'openWordForms':141447,'curatedRules':512},
            'templates':{'titles':262144,'descriptions':262144,'tagSets':262144,'baseLanguageBlocks':128,'genericTagAtoms':48}}))
        searches=[]
        def search(route):
            data=route.request.post_data_json;searches.append(data)
            offset=data['offset']
            route.fulfill(json={'entries':[{'ru':f'стол {i}','zh':f'书桌{i}','sourceId':'fixture-source','reviewed':False} for i in range(offset,min(offset+40,91))],'total':91,'offset':offset})
        self.page.route('**/api/local/language/search',search)
        self.open_editor()
        self.assertFalse(any(url.endswith('/api/local/language') for url in requests),'Large dictionary capabilities must be lazy')
        self.page.locator('nav [data-tab="rules"]').click()
        self.page.wait_for_function(r"document.getElementById('languageStats').textContent.replace(/\s/g,'').includes('89171')")
        self.assertIn('схем',self.page.locator('#languageKnowledge').inner_text().lower())
        self.page.locator('#dictionarySearch').fill('стол')
        self.page.locator('#dictionaryResults .language-result').nth(39).wait_for()
        self.assertEqual(len(searches),1)
        self.assertEqual(searches[0],{'query':'стол','scope':'all','offset':0,'limit':40})
        self.assertEqual(self.page.locator('#dictionaryResults .language-result').count(),40)
        self.assertIn('нужна проверка',self.page.locator('#dictionaryResults').inner_text())
        self.page.locator('#dictionaryNext').click()
        self.page.wait_for_function("document.querySelector('#dictionaryResults strong').textContent==='стол 40'")
        self.assertEqual(self.page.locator('#dictionaryResults .language-result').count(),40)
        self.assertEqual(searches[-1]['offset'],40)
        self.page.locator('#dictionaryScope').select_option('rental')
        self.page.wait_for_function("document.querySelector('#dictionaryResults strong').textContent==='стол 0'")
        self.assertEqual(searches[-1]['scope'],'rental');self.assertEqual(searches[-1]['offset'],0)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
        self.assertGreaterEqual(self.page.locator('#dictionarySearch').evaluate('el=>parseFloat(getComputedStyle(el).fontSize)'),16)

    def test_translation_requires_apply_and_preserves_other_output_parts(self):
        self.page.route('**/api/local/translate',lambda route:route.fulfill(json={
            'text':'厨房有书桌。','unknown':['необычное слово'],'warnings':['Проверьте порядок слов'],'needsReview':True,'backend':'rental-phrases'}))
        self.open_editor()
        self.page.locator('#outputTitle').fill('原来的标题')
        self.page.locator('#output').fill('原来的正文')
        self.page.locator('#tags').fill('#原来的标签')
        self.page.locator('#languageComposer summary').click()
        self.page.locator('#languageRussianInput').fill('На кухне есть стол и необычное слово.')
        self.page.locator('#languageTranslate').click()
        self.page.wait_for_function("document.getElementById('languageChineseOutput').value==='厨房有书桌。'")
        self.assertEqual(self.page.locator('#output').input_value(),'原来的正文')
        self.assertIn('непереведённых фрагментов: 1',self.page.locator('#languageReview').inner_text())
        self.page.locator('#languageChineseOutput').fill('厨房里有餐桌。')
        self.page.locator('#languageApply').click()
        self.page.wait_for_function("document.getElementById('output').value==='厨房里有餐桌。'")
        self.assertEqual(self.page.locator('#outputTitle').input_value(),'原来的标题')
        self.assertEqual(self.page.locator('#tags').input_value(),'#原来的标签')
        self.assertGreaterEqual(self.page.locator('#languageApply').bounding_box()['height'],44)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_stale_translation_cannot_replace_manual_preview(self):
        pending=[]
        self.page.route('**/api/local/translate',lambda route:pending.append(route))
        self.open_editor()
        self.page.locator('#languageComposer summary').click()
        self.page.locator('#languageRussianInput').fill('Первый вариант описания.')
        self.page.locator('#languageTranslate').click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(pending),1)
        self.page.locator('#languageChineseOutput').fill('我保留的手动修改。')
        pending[0].fulfill(json={'text':'过时的回复。','unknown':[],'needsReview':True})
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.locator('#languageChineseOutput').input_value(),'我保留的手动修改。')
        self.assertIn('вручную',self.page.locator('#languageReview').inner_text())
        self.assertFalse(self.page.locator('#languageApply').is_disabled())

    def test_independent_rewrites_preserve_manual_other_parts_and_new_card_clears_draft(self):
        self.open_editor()
        for key,value in {'price':'80000','area':'54','rooms':'2','metro':'Юго-Западная','walk':'8'}.items():
            self.page.locator('#'+key).fill(value)
        self.page.locator('#outputTitle').fill('手动标题')
        self.page.locator('#output').fill('手动保留的正文。')
        self.page.locator('#tags').fill('#手动标签')
        self.page.locator('#rotateTitles').click()
        self.assertNotEqual(self.page.locator('#outputTitle').input_value(),'手动标题')
        self.assertEqual(self.page.locator('#output').input_value(),'手动保留的正文。')
        self.assertEqual(self.page.locator('#tags').input_value(),'#手动标签')
        new_title=self.page.locator('#outputTitle').input_value()
        self.page.on('dialog',lambda dialog:dialog.accept())
        self.page.locator('#rewriteCopy').click()
        self.assertEqual(self.page.locator('#outputTitle').input_value(),new_title)
        self.assertNotEqual(self.page.locator('#output').input_value(),'手动保留的正文。')
        self.assertEqual(self.page.locator('#tags').input_value(),'#手动标签')
        new_body=self.page.locator('#output').input_value()
        self.page.locator('#rotateTags').click()
        self.assertEqual(self.page.locator('#outputTitle').input_value(),new_title)
        self.assertEqual(self.page.locator('#output').input_value(),new_body)
        self.assertNotEqual(self.page.locator('#tags').input_value(),'#手动标签')
        self.page.locator('#languageComposer summary').click()
        self.page.locator('#languageRussianInput').fill('Приватный незавершённый черновик.')
        self.page.locator('#languageChineseOutput').fill('未完成的私人草稿。')
        self.page.locator('#newObject').click()
        self.assertEqual(self.page.locator('#languageRussianInput').input_value(),'')
        self.assertEqual(self.page.locator('#languageChineseOutput').input_value(),'')

    def test_v5_migration_keeps_custom_priority_and_only_renders_one_rule_page(self):
        self.page.add_init_script("""localStorage.setItem('most-rules-version','4');
          localStorage.setItem('most-rules',JSON.stringify(Object.assign(
            {'Москва':'自定义莫斯科','МГУ':'莫斯科国立大学'},
            Object.fromEntries(Array.from({length:55},(_,i)=>['Моя фраза '+i,'我的表达'+i])))));""")
        self.page.route('**/api/local/language',lambda route:route.fulfill(json={'dictionary':{},'templates':{}}))
        self.open_editor()
        self.page.locator('nav [data-tab="rules"]').click()
        self.assertLessEqual(self.page.locator('#ruleList>div').count(),40)
        self.assertGreater(self.page.locator('#ruleList>div').count(),0)
        stored=self.page.evaluate("JSON.parse(localStorage.getItem('most-rules'))")
        self.assertEqual(stored['Москва'],'自定义莫斯科')
        self.assertNotIn('МГУ',stored,'An unchanged v4 built-in must not become a personal override')
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('most-rules-version'))"),5)


if __name__=='__main__':unittest.main()
