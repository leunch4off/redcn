"""Optional real Chrome checks for explicit school/campus reference selection."""
import json
from pathlib import Path
import threading
import unittest

import test_phone_browser as acceptance
from browser_text_jobs_fixture import MockTextJobs, accepted_draft


class UniversityReferenceAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        acceptance.PhoneBrowserAcceptance.setUp(self)
        self.desktop_thread=threading.Thread(target=self.controller.serve_forever,daemon=True)
        self.desktop_thread.start()
        self.desktop='http://127.0.0.1:'+str(self.controller.server_port)
        catalog=json.loads((Path(__file__).resolve().parents[1]/'data/moscow-universities.json').read_text())
        self.school=next(row for row in catalog['universities'] if 'МГУ' in [row['ru'],*row.get('aliases',[])])
        self.campus=next(row for row in catalog['campuses'] if row['universityId']==self.school['id'])
        self.other_school=next(row for row in catalog['universities'] if row['id']!=self.school['id'])

    def tearDown(self):
        self.controller.shutdown();self.desktop_thread.join()
        acceptance.PhoneBrowserAcceptance.tearDown(self)

    def open_editor(self):
        self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port),wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")

    def test_autocomplete_is_local_and_selection_preserves_apartment_and_route(self):
        calls=[]
        self.page.on('request',lambda request:calls.append(request.url))
        self.open_editor()
        self.assertFalse(any('/api/local/universities' in url for url in calls))
        self.page.locator('#price').fill('80000');self.page.locator('#metro').fill('Саларьево');self.page.locator('#walk').fill('12')
        self.page.locator('#university').fill('МГУ')
        self.page.locator('#universitySuggestions button').first.click()
        self.assertEqual(self.page.locator('#university').input_value(),self.school['ru'])
        self.assertEqual(self.page.locator('#price').input_value(),'80000')
        self.assertEqual(self.page.locator('#metro').input_value(),'Саларьево')
        self.assertEqual(self.page.locator('#walk').input_value(),'12')
        self.assertEqual(self.page.locator('#uniTime').input_value(),'')
        self.assertEqual(self.page.locator('#uniMode').input_value(),'unknown')
        self.assertFalse(any('/api/local/universities' in url for url in calls))

    def test_campus_selection_fills_its_parent_but_not_apartment_coordinates(self):
        self.open_editor()
        self.page.locator('#price').fill('95000');self.page.locator('#metro').fill('Потапово')
        self.page.locator('#campus').fill(self.campus['ru'])
        self.page.locator('#campusSuggestions button').first.click()
        self.assertEqual(self.page.locator('#campus').input_value(),self.campus['ru'])
        self.assertEqual(self.page.locator('#university').input_value(),self.school['ru'])
        self.assertEqual(self.page.locator('#metro').input_value(),'Потапово')
        self.assertEqual(self.page.locator('#latitude').input_value(),'')
        self.assertEqual(self.page.locator('#longitude').input_value(),'')
        self.assertEqual(self.page.locator('#uniTime').input_value(),'')
        self.assertEqual(self.page.locator('#price').input_value(),'95000')

    def test_knowledge_uses_protected_search_and_shows_sources_without_mobile_overflow(self):
        self.open_editor()
        calls=[]
        self.page.on('request',lambda request:calls.append(request.url))
        self.page.locator('nav [data-tab="knowledge"]').click()
        self.page.locator('#universitySearch').fill('МГУ')
        self.page.locator('#universityKind').select_option('campus')
        self.page.locator('#universityResults [data-reference-kind="campus"]').first.wait_for()
        self.page.wait_for_function("document.getElementById('universityPageStatus').textContent.startsWith('Найдено')")
        self.assertTrue(any('/api/local/universities/search' in url for url in calls))
        self.assertTrue(any('/api/local/universities' in url for url in calls))
        self.assertLessEqual(self.page.locator('#universityResults article').count(),40)
        self.assertTrue(self.page.locator('#universityResults a.university-source').first.get_attribute('href').startswith('http'))
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
        self.assertGreaterEqual(self.page.locator('#universitySearch').evaluate('el=>parseFloat(getComputedStyle(el).fontSize)'),16)

    def test_switching_school_clears_only_a_known_campus_of_the_previous_school(self):
        self.open_editor();self.page.locator('#price').fill('95000');self.page.locator('#metro').fill('Потапово')
        self.page.locator('#campus').fill(self.campus['ru']);self.page.locator('#campusSuggestions button').first.click()
        self.page.locator('#university').fill(self.other_school['ru']);self.page.locator('#universitySuggestions button').first.click()
        self.assertEqual(self.page.locator('#university').input_value(),self.other_school['ru'])
        self.assertEqual(self.page.locator('#campus').input_value(),'')
        self.assertEqual(self.page.locator('#price').input_value(),'95000');self.assertEqual(self.page.locator('#metro').input_value(),'Потапово')
        self.page.locator('#campus').fill('Мой непроверенный кампус')
        self.page.locator('#university').fill('МГУ');self.page.locator('#universitySuggestions button').first.click()
        self.assertEqual(self.page.locator('#campus').input_value(),'Мой непроверенный кампус')

    def test_chinese_statistics_and_personal_accounts_keep_historical_scope_without_changing_apartment(self):
        self.open_editor();self.page.locator('#price').fill('81000');self.page.locator('#metro').fill('Прокшино')
        self.page.locator('nav [data-tab="knowledge"]').click();self.page.locator('#universityKind').select_option('university');self.page.locator('#universitySearch').fill('РУДН')
        rudn=self.page.locator('#universityResults [data-reference-id="rudn"]');rudn.wait_for();rudn.locator('.university-china-data > summary').click()
        text=rudn.inner_text();self.assertRegex(text,r'1\s612 студентов из КНР');self.assertIn('2024-06-20',text);self.assertIn('2026-11-01',text);self.assertIn('蔡怡清',text);self.assertIn('沉潇婷',text);self.assertIn('не имеют общего знаменателя',text)
        self.page.locator('#universitySearch').fill('МАИ');mai=self.page.locator('#universityResults [data-reference-id="mai"]');mai.wait_for();mai.locator('.university-china-data > summary').click()
        self.assertIn('Более 650',mai.inner_text());self.assertIn('Гостевая летняя школа',mai.inner_text());self.assertIn('не доказывают результаты обучения',mai.inner_text())
        self.assertEqual(self.page.locator('#price').input_value(),'81000');self.assertEqual(self.page.locator('#metro').input_value(),'Прокшино');self.assertEqual(self.page.locator('#uniTime').input_value(),'');self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_selected_school_reaches_individual_job_and_applies_checked_reference_copy(self):
        # Deterministic UI transport fixture; this is not a model-quality test.
        def authored_text(request,ordinal):
            fields=request['fields']
            self.assertEqual(fields['university'],self.school['ru'])
            self.assertEqual(fields['uniTime'],'')
            self.assertEqual(fields['uniMode'],'unknown')
            result=accepted_draft(request,ordinal)
            result['title']='莫斯科房源｜通勤目标：'+self.school['zh']
            result['body']+='\n通勤目标：'+self.school['zh']+'。时间待确认。'
            return result
        text_jobs=MockTextJobs(self.page,'local',authored_text)
        self.open_editor()
        self.page.locator('#price').fill('80000');self.page.locator('#area').fill('50');self.page.locator('#rooms').fill('2')
        self.page.locator('#university').fill('МГУ');self.page.locator('#universitySuggestions button').first.click()
        self.page.locator('#generate').click()
        self.page.wait_for_function("document.getElementById('output').value.includes('时间待确认')")
        self.assertEqual(len(text_jobs.requests),1)
        title=self.page.locator('#outputTitle').input_value();body=self.page.locator('#output').input_value()
        self.assertIn(self.school['zh'],title);self.assertIn(self.school['zh'],body)
        self.assertIn('通勤目标',title);self.assertIn('时间待确认',body)
        self.assertNotIn('近地铁',title);self.assertNotIn('步行约',body)
        self.assertNotIn('抵达登记（临时居住登记）',body,'Unconfirmed service copy must not be introduced by this UI fixture')
