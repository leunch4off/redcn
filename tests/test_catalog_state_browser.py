"""Real built desktop/phone shells against disposable authenticated servers.

Chrome with phone viewport is not a claim of physical iPhone acceptance.
"""
import copy
import json
import threading
import unittest

import test_phone_browser as fixture
import local_server
import phone_relay


class CatalogStateBrowserTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    tearDown = fixture.PhoneBrowserAcceptance.tearDown
    connect = fixture.PhoneBrowserAcceptance.connect

    def setUp(self):
        fixture.PhoneBrowserAcceptance.setUp(self)
        newer = copy.deepcopy(self.result)
        newer['titles'] = ['新加入的房源']
        newer['fields']['price'] = '90000'
        self.newer = local_server.save_result(self.directory, newer)

    def ids(self, selector):
        return self.page.locator(selector).evaluate_all('(nodes)=>nodes.map(node=>node.dataset.cardId)')

    def test_desktop_view_gray_stable_order_and_edited_tab_survive_reload(self):
        self.page.set_viewport_size({'width': 1300, 'height': 950})
        thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        thread.start()
        try:
            self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port))
            self.page.locator('[data-tab="library"]').click()
            rows = '#macCardList [data-card-id]'
            self.page.locator(rows).first.wait_for()
            self.assertEqual(self.ids(rows), [self.newer, self.card_id])
            with self.page.expect_response(lambda response: response.url.endswith('/cards/'+self.card_id+'/viewed')) as response:
                self.page.locator('#macCardList [data-card-id="'+self.card_id+'"] a').click()
            self.assertEqual(response.value.status, 200)
            self.page.wait_for_function("document.querySelector('#importStatus').textContent.includes('Сохранённая карточка загружена')")
            self.page.locator('#output').fill('独立卧室，照片中可以看到床和衣柜。')
            with self.page.expect_response(lambda response: response.url.endswith('/api/local/save') and response.request.method == 'POST'):
                self.page.locator('#saveLocal').click()
            self.page.wait_for_function("document.querySelector('#localSaveStatus').textContent.startsWith('Сохранено')")
            self.page.locator('[data-tab="library"]').click()
            self.page.locator(rows).first.wait_for()
            self.assertEqual(self.ids(rows), [self.newer, self.card_id])
            viewed = self.page.locator('#macCardList [data-card-id="'+self.card_id+'"]')
            self.assertIn('catalog-viewed', viewed.get_attribute('class'))
            self.assertIn('Просмотрено', viewed.inner_text())
            self.page.locator('[data-mac-card-filter="edited"]').click()
            self.assertEqual(self.ids(rows), [self.card_id])
            self.page.reload()
            self.page.wait_for_function("document.querySelector('#importStatus').textContent.includes('Сохранённая карточка загружена')")
            self.page.locator('[data-tab="library"]').click()
            self.page.locator(rows).first.wait_for()
            self.assertEqual(self.ids(rows), [self.newer, self.card_id])
            self.assertIn('catalog-viewed', self.page.locator('#macCardList [data-card-id="'+self.card_id+'"]').get_attribute('class'))
            self.page.locator('[data-mac-card-filter="edited"]').click()
            self.assertEqual(self.ids(rows), [self.card_id])
        finally:
            self.controller.shutdown()
            thread.join()

    def test_phone_viewed_edit_filter_and_original_order_persist(self):
        self.connect()
        rows = '#phoneCards [data-card-id]'
        self.assertEqual(self.ids(rows), [self.newer, self.card_id])

        with self.page.expect_response(lambda response: response.url.endswith('/cards/'+self.card_id+'/viewed')) as response:
            self.page.locator('#phoneCards [data-card-id="'+self.card_id+'"]').click()
        self.assertEqual(response.value.status, 200)
        self.page.locator('#phoneDetail').wait_for(state='visible')
        self.page.locator('#phoneBack').click()
        viewed = self.page.locator('#phoneCards [data-card-id="'+self.card_id+'"]')
        self.page.wait_for_function('id=>document.querySelector(\'#phoneCards [data-card-id="\'+id+\'"]\').classList.contains("catalog-viewed")', arg=self.card_id)
        self.assertIn('Просмотрено', viewed.inner_text())
        changed = copy.deepcopy(self.result); changed['body'] = '保存后更新的房源描述。'
        local_server.save_result(self.directory, changed, self.card_id)
        self.page.locator('#phoneRefresh').click()
        self.page.wait_for_function('id=>document.querySelector(\'#phoneCards [data-card-id="\'+id+\'"]\').textContent.includes("Текст изменён")', arg=self.card_id)
        self.assertEqual(self.ids(rows), [self.newer, self.card_id])
        self.page.locator('[data-phone-card-filter="edited"]').click()
        self.assertEqual(self.ids(rows), [self.card_id])
        self.page.reload()
        self.page.locator(rows).first.wait_for()
        self.assertEqual(self.ids(rows), [self.newer, self.card_id])
        self.assertIn('catalog-viewed', self.page.locator('#phoneCards [data-card-id="'+self.card_id+'"]').get_attribute('class'))
        self.assertFalse(next(row for row in phone_relay.list_cards(self.directory)['cards'] if row['id']==self.card_id)['viewed'])
        fixture.grant_paired_role(self, 'owner')
        self.page.locator('#phoneRefresh').click()
        self.page.wait_for_function('id=>!document.querySelector(\'#phoneCards [data-card-id="\'+id+\'"]\').classList.contains("catalog-viewed")', arg=self.card_id)
        self.assertEqual(self.ids(rows), [self.newer, self.card_id])

    def test_phone_delayed_feed_and_view_ack_do_not_undo_view_or_edit(self):
        self.connect()
        held_feed = []
        feed_url = self.base+'/api/phone/cards'

        def hold_feed(route):
            response = route.fetch()
            held_feed.append((route, response))
            self.page.evaluate('window.catalogOldFeedHeld=true')

        self.page.route(feed_url, hold_feed)
        self.page.locator('#phoneRefresh').click()
        self.page.wait_for_function('window.catalogOldFeedHeld===true')
        old_rows = held_feed[0][1].json()['cards']
        self.assertFalse(next(row for row in old_rows if row['id']==self.card_id)['viewed'])
        with self.page.expect_response(lambda response: response.url.endswith('/cards/'+self.card_id+'/viewed')):
            self.page.locator('#phoneCards [data-card-id="'+self.card_id+'"]').click()
        self.page.wait_for_function('id=>document.querySelector(\'#phoneCards [data-card-id="\'+id+\'"]\').classList.contains("catalog-viewed")', arg=self.card_id)
        self.page.locator('#phoneBack').click()
        held_feed[0][0].fulfill(response=held_feed[0][1])
        self.page.wait_for_function('!document.getElementById("phoneRefresh").disabled')
        self.assertIn('catalog-viewed', self.page.locator('#phoneCards [data-card-id="'+self.card_id+'"]').get_attribute('class'))
        self.page.unroute(feed_url, hold_feed)

        held_ack = []
        ack_url = self.base+'/api/phone/cards/'+self.newer+'/viewed'

        def hold_ack(route):
            response = route.fetch()
            held_ack.append((route, response))
            self.page.evaluate('window.catalogOldAckHeld=true')

        self.page.route(ack_url, hold_ack)
        self.page.locator('#phoneCards [data-card-id="'+self.newer+'"]').click()
        self.page.wait_for_function('window.catalogOldAckHeld===true')
        self.assertFalse(held_ack[0][1].json()['contentEdited'])
        self.page.locator('#phoneBack').click()
        changed = local_server.read_result(self.directory, self.newer)
        changed['body'] = '比查看回执更新的保存内容。'
        local_server.save_result(self.directory, changed, self.newer)
        self.page.locator('#phoneRefresh').click()
        self.page.wait_for_function('id=>document.querySelector(\'#phoneCards [data-card-id="\'+id+\'"]\').textContent.includes("Текст изменён")', arg=self.newer)
        held_ack[0][0].fulfill(response=held_ack[0][1])
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        self.page.unroute(ack_url, hold_ack)
        self.assertIn('Текст изменён', self.page.locator('#phoneCards [data-card-id="'+self.newer+'"]').inner_text())
        self.page.locator('[data-phone-card-filter="edited"]').click()
        self.assertEqual(self.ids('#phoneCards [data-card-id]'), [self.newer])

    def test_desktop_delayed_view_ack_cannot_remove_newer_edited_state(self):
        self.page.set_viewport_size({'width': 1300, 'height': 950})
        thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        thread.start()
        held_ack = []
        ack_url = 'http://127.0.0.1:'+str(self.controller.server_port)+'/api/local/cards/'+self.card_id+'/viewed'

        def hold_ack(route):
            response = route.fetch()
            held_ack.append((route, response))
            self.page.evaluate('window.catalogDesktopAckHeld=true')

        self.page.route(ack_url, hold_ack)
        try:
            self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port, self.card_id))
            self.page.wait_for_function('window.catalogDesktopAckHeld===true')
            self.assertFalse(held_ack[0][1].json()['contentEdited'])
            changed = copy.deepcopy(self.result); changed['body'] = '比查看回执更新的桌面保存内容。'
            local_server.save_result(self.directory, changed, self.card_id)
            self.page.locator('[data-tab="library"]').click()
            target = '#macCardList [data-card-id="'+self.card_id+'"]'
            self.page.wait_for_function('selector=>document.querySelector(selector)?.textContent.includes("Текст изменён")', arg=target)
            held_ack[0][0].fulfill(response=held_ack[0][1])
            self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            self.assertIn('Текст изменён', self.page.locator(target).inner_text())
            self.assertIn('catalog-viewed', self.page.locator(target).get_attribute('class'))
            self.page.locator('[data-mac-card-filter="edited"]').click()
            self.assertEqual(self.ids('#macCardList [data-card-id]'), [self.card_id])
        finally:
            self.page.unroute(ack_url, hold_ack)
            self.controller.shutdown()
            thread.join()

    def test_phone_deep_link_view_survives_first_appearance_in_an_older_feed(self):
        # A notification/import result can open a card outside the loaded page.
        # Keep the server's actual pre-view response and deliver it afterwards.
        self.connect()
        feed_url = self.base+'/api/phone/cards'
        snapshots = []

        def delayed_page(route):
            if not snapshots:
                response = route.fetch()
                snapshot = response.json()
                snapshots.append(snapshot)
                partial = {**snapshot, 'cards': [row for row in snapshot['cards'] if row['id'] != self.card_id]}
                route.fulfill(response=response, body=json.dumps(partial))
            else:
                route.fulfill(status=200, content_type='application/json', body=json.dumps(snapshots[0]))

        self.page.route(feed_url, delayed_page)
        try:
            with self.page.expect_request_finished(lambda request: request.url.endswith('/cards/'+self.card_id+'/viewed')) as viewed:
                self.page.goto(self.base+'/phone/?card='+self.card_id)
            self.assertEqual(viewed.value.response().status, 200)
            self.page.locator('#phoneDetail').wait_for(state='visible')
            self.assertNotIn(self.card_id, self.ids('#phoneCards [data-card-id]'))
            self.assertFalse(next(row for row in snapshots[0]['cards'] if row['id'] == self.card_id)['viewed'])
            self.page.locator('#phoneBack').click()
            self.page.locator('#phoneRefresh').click()
            target = '#phoneCards [data-card-id="'+self.card_id+'"]'
            self.page.wait_for_function('selector=>document.querySelector(selector)?.classList.contains("catalog-viewed")', arg=target)
            self.assertIn('Просмотрено', self.page.locator(target).inner_text())
            self.assertEqual(self.ids('#phoneCards [data-card-id]'), [self.newer, self.card_id])
        finally:
            self.page.unroute(feed_url, delayed_page)

    def test_phone_repair_same_role_saves_its_own_view_without_old_session_ack(self):
        self.connect()
        target = '#phoneCards [data-card-id="'+self.card_id+'"]'
        with self.page.expect_response(lambda response: response.url.endswith('/cards/'+self.card_id+'/viewed')):
            self.page.locator(target).click()
        self.page.wait_for_function('selector=>document.querySelector(selector)?.classList.contains("catalog-viewed")', arg=target)
        self.page.locator('#phoneBack').click()

        sessions = self.phone.pairing.list_sessions()
        self.phone.pairing.manage_session(sessions['sessions'][0]['id'], revoke=True, expected_revision=sessions['revision'])
        self.page.locator('#phoneRefresh').click()
        self.page.locator('#phonePair').wait_for(state='visible')
        # Re-pair in the same document: a reload would hide leaked JS state.
        code, _ = self.phone.pairing.issue()
        self.page.locator('#phonePairCode').fill(code)
        self.page.locator('#phonePairSubmit').click()
        self.page.locator(target).wait_for(state='visible')
        self.assertNotIn('catalog-viewed', self.page.locator(target).get_attribute('class'))
        with self.page.expect_response(lambda response: response.url.endswith('/cards/'+self.card_id+'/viewed')) as viewed:
            self.page.locator(target).click()
        self.assertEqual(viewed.value.status, 200)
        self.page.wait_for_function('selector=>document.querySelector(selector)?.classList.contains("catalog-viewed")', arg=target)
        self.page.locator('#phoneBack').click()
        self.page.reload()
        self.page.locator(target).wait_for(state='visible')
        self.assertIn('catalog-viewed', self.page.locator(target).get_attribute('class'))

    def test_phone_cold_reload_drains_feed_pages_and_keeps_edited_old_card_in_place(self):
        expected = [self.newer, self.card_id]
        for number in range(203):
            value = copy.deepcopy(self.result)
            value['fields']['photos'] = []
            value['titles'] = ['分页房源 '+str(number)]
            expected.insert(0, local_server.save_result(self.directory, value))
        self.connect()
        self.page.wait_for_function('document.querySelectorAll("#phoneCards [data-card-id]").length===205')
        self.page.wait_for_function('!document.getElementById("phoneRefresh").disabled')
        self.assertEqual(self.ids('#phoneCards [data-card-id]'), expected)
        changed = copy.deepcopy(self.result)
        changed['body'] = '早期房源的描述已编辑，但位置保持不变。'
        local_server.save_result(self.directory, changed, self.card_id)
        self.page.reload()
        self.page.wait_for_function('document.querySelectorAll("#phoneCards [data-card-id]").length===205')
        self.page.wait_for_function('!document.getElementById("phoneRefresh").disabled')
        self.assertEqual(self.ids('#phoneCards [data-card-id]'), expected)
        self.assertIn('Текст изменён', self.page.locator('#phoneCards [data-card-id="'+self.card_id+'"]').inner_text())



if __name__ == '__main__':
    unittest.main()
