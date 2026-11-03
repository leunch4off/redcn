"""Fixture-only DOM acceptance for advertising supervision; never runs a model."""
import copy
import unittest

import test_learning_studio_browser as learning_harness


def overview():
    return {
        'status': {'counts': {'pending': 1, 'approved': 30}, 'styleOnlyExamples': 25,
                   'activeLane': 'advertising', 'lanes': {'advertising': {
                       'approvedExamples': 4, 'approvedRecords': 5, 'approvedGroups': 4,
                       'pendingExamples': 2, 'trainExamples': 3, 'validExamples': 1,
                       'minimumExamples': 20, 'minimumGroups': 5,
                       'minimumTrain': 16, 'minimumValidation': 4,
                       'trainingEligible': False,
                       'blockingReasons': ['reviewed-examples-needed', 'validation-split-needed'],
                       'assistantReviewed': 5, 'humanVerified': 0}}},
        'scheduler': {'lane': 'advertising', 'phase': 'training', 'workerAlive': False,
                      'modelProcessRunning': False, 'stepsCompleted': 0, 'promotion': {}},
        'text_ai': {'model': 'Qwen2.5-0.5B-Instruct', 'stepsCompleted': 800,
                    'candidateAdapterSelected': True, 'advertising': {
                        'model': 'Qwen3-4B-Instruct-2507', 'installed': True,
                        'supported': True, 'enabled': True, 'separateProfile': True,
                        'candidateAdapterSelected': False, 'adapterActive': False,
                        'adapterCompatible': False, 'trainedByRedcn': False}},
        'events': []}


class GroundedLearningBrowserTests(unittest.TestCase):
    # Reuse the isolated HTTP/Chrome lifecycle, without inheriting its test methods.
    setUpClass = classmethod(learning_harness.LearningStudioBrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(learning_harness.LearningStudioBrowserTests.tearDownClass.__func__)
    setUp = learning_harness.LearningStudioBrowserTests.setUp
    tearDown = learning_harness.LearningStudioBrowserTests.tearDown

    def install_overview(self, response):
        writes = []

        def respond(route):
            data = route.request.post_data_json or {}
            if data.get('action') in ('record-grounded', 'review-grounded'):
                writes.append(copy.deepcopy(data))
                route.fulfill(json={'event': {'id': 'fixture-saved', 'status': 'approved'}})
            else:
                route.fulfill(json=response)

        self.page.route('**/api/learning', respond)
        self.page.evaluate('studio.refresh()')
        return writes

    def test_advertising_status_separates_translation_history_and_live_process_proof(self):
        response = overview()
        self.install_overview(response)
        panel = self.page.locator('.learning-advertising-status')
        text = panel.inner_text()
        self.assertIn('4 / 20', text)
        self.assertIn('3 / 16', text)
        self.assertIn('1 / 4', text)
        self.assertIn('Проверено человеком: 0', text)
        self.assertIn('проверено ассистентом: 5', text)
        self.assertIn('не подтверждена', text)
        self.assertNotIn('800', text)
        self.assertNotIn('0.5B', text)
        self.assertNotIn('Сейчас выполняется обучение', text)
        self.assertIn('история весов перевода: 800 шагов', self.page.locator('body').inner_text())
        response['scheduler'].update(modelProcessRunning=True, stepsCompleted=12,
                                     trainingProgress={'stepsCompleted': 12, 'requestedSteps': 80})
        self.page.evaluate('studio.refresh()')
        self.assertIn('Сейчас выполняется обучение адаптера объявлений', panel.inner_text())
        self.assertIn('12 / 80 шагов', panel.inner_text())
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))

    def test_pair_save_binds_review_to_current_facts_and_never_sends_authorship(self):
        writes = self.install_overview(overview())
        self.page.evaluate('''() => {
          window.fields={city:'Москва',rooms:'1',area:'38',price:'65000',
            sourceDescription:'Без животных.',photoEvidence:{bedAny:{status:'confirmed'}},
            source:'PRIVATE_RAW_HTML',ownerToken:'PRIVATE_TOKEN'};
          window.output={title:'莫斯科38㎡一居室',body:'莫斯科38㎡一居室，月租65000₽。',tags:''};
        }''')
        load = self.page.get_by_role('button', name='Загрузить текущую карточку для сверки', exact=True)
        save = self.page.get_by_role('button', name='Сохранить проверенную пару', exact=True)
        reason = self.page.get_by_label('Что сверено с исходными фактами', exact=True)
        reviewed = self.page.get_by_label('Я сверил весь заголовок и описание с исходными фактами этой квартиры', exact=True)
        load.click()
        self.assertTrue(save.is_disabled())
        reason.fill('Цена, ограничения и мебель сверены с источником.')
        reviewed.check()
        self.page.evaluate("fields.price='70000'")
        save.click()
        self.page.wait_for_function("document.querySelector('.learning-studio').getAttribute('aria-busy')==='false'")
        self.assertEqual(writes, [])
        self.assertFalse(reviewed.is_checked())
        self.assertIn('Факты или выбранная карточка изменились', self.page.locator('body').inner_text())
        load.click()
        self.page.get_by_label('Полное проверенное описание пары', exact=True).fill('莫斯科38㎡一居室，月租70000₽。不接受宠物。')
        reason.fill('Сверены обновлённая цена 70000₽, запрет животных и подтверждения предметов.')
        reviewed.check()
        save.click()
        self.page.wait_for_function("document.querySelector('.learning-studio').getAttribute('aria-busy')==='false'")
        self.assertEqual(len(writes), 1)
        data = writes[0]
        self.assertEqual(data['action'], 'record-grounded')
        self.assertEqual(data['fields']['price'], '70000')
        self.assertEqual(data['fields']['sourceDescription'], 'Без животных.')
        self.assertEqual(data['groupId'], 'listing-one')
        self.assertIs(data['reviewed'], True)
        for key in ('actor', 'humanVerified', 'rules'):
            self.assertNotIn(key, data)
        for key in ('source', 'ownerToken'):
            self.assertNotIn(key, data['fields'])
        self.assertFalse(self.page.evaluate('studio.activity().dirty'))
        self.assertFalse(self.page.evaluate('studio.activity().busy'))
        self.assertEqual(self.page.evaluate('applied'), [])

    def test_pending_pair_preserves_full_text_and_requires_review_after_manual_changes(self):
        response = overview()
        full_body = '完整正文。' * 600 + 'THE_TARGET_END'
        response['events'] = [{'id': 'grounded-fixture', 'kind': 'grounded_listing',
            'status': 'pending', 'payload': {
                'fields': {'sourceDescription': 'SOURCE_END <img src=x onerror="window.leak=true">',
                           'city': 'Москва', 'price': 65000},
                'request': {'messages': [{'role': 'user', 'content': '月租：65000₽/月\n配置：床。'}]},
                'target': {'title': '莫斯科38㎡一居室', 'body': full_body},
                'review': {'actor': 'assistant_editorial_fixture', 'humanVerified': False,
                           'reason': 'Fixture editorial review; not human verification.'}}}]
        writes = self.install_overview(response)
        row = self.page.locator('.learning-studio article').first
        self.assertEqual(row.get_by_label('Полное описание проверяемой записи', exact=True).input_value(), full_body)
        self.assertIn('SOURCE_END', row.inner_text())
        self.assertIn('Проверка ассистента', row.inner_text())
        self.assertEqual(row.get_by_role('button', name='Утвердить', exact=True).count(), 0)
        self.assertIsNone(self.page.evaluate('window.leak'))
        self.assertEqual(row.locator('img').count(), 0)
        row.get_by_text('Все параметры и подтверждения этой квартиры', exact=True).click()
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        reason = row.get_by_label('Основание проверки этой записи', exact=True)
        verified = row.get_by_label('Я сверил весь текст этой записи с её исходными фактами', exact=True)
        save = row.get_by_role('button', name='Сохранить сверенный текст для обучения', exact=True)
        reason.fill('Сверены все условия и предметы из исходного описания.')
        verified.check()
        edited = full_body + ' 手工修改。'
        row.get_by_label('Полное описание проверяемой записи', exact=True).fill(edited)
        self.assertFalse(verified.is_checked())
        self.assertTrue(save.is_disabled())
        verified.check()
        save.click()
        self.page.wait_for_function("document.querySelector('.learning-studio').getAttribute('aria-busy')==='false'")
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['action'], 'review-grounded')
        self.assertEqual(writes[0]['id'], 'grounded-fixture')
        self.assertEqual(writes[0]['target']['body'], edited)
        self.assertIs(writes[0]['reviewed'], True)
        self.assertNotIn('actor', writes[0])


if __name__ == '__main__':
    unittest.main()
