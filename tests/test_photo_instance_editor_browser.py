"""Real browser/API review of one visible object across two synthetic angles."""
import unittest

import test_phone_browser as fixture
import photo_instances


class PhotoInstanceEditorBrowserTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp = fixture.PhoneBrowserAcceptance.setUp
    tearDown = fixture.PhoneBrowserAcceptance.tearDown

    def draw(self, panel, start=(.1, .1), end=(.4, .6)):
        image = panel.locator('img')
        image.wait_for(state='visible')
        image.scroll_into_view_if_needed()
        image.evaluate('node=>node.decode()')
        bounds = image.bounding_box()
        self.page.mouse.move(bounds['x']+start[0]*bounds['width'], bounds['y']+start[1]*bounds['height'])
        self.page.mouse.down()
        self.page.mouse.move(bounds['x']+end[0]*bounds['width'], bounds['y']+end[1]*bounds['height'], steps=3)
        self.page.mouse.up()

    def review(self):
        return photo_instances.load_review(self.directory, self.card_id,
            photo_ids=[row['id'] for row in self.assets])

    def test_two_angles_one_instance_and_visible_wrong_merge_do_not_rewrite_listing(self):
        fixture.PhoneBrowserAcceptance.connect(self)
        self.page.locator('#phoneCards .listing-card').first.click()
        panel = self.page.locator('details').filter(has=self.page.get_by_text('Экземпляры мебели · сопоставить ракурсы', exact=True))
        panel.locator('summary').click()
        panel.get_by_label('Вид предмета').wait_for()
        for photo in ('0', '1'):
            panel.get_by_label('Исходное фото для сопоставления').select_option(photo)
            panel.get_by_label('Вид предмета').select_option('chairs')
            panel.get_by_label('ID того же экземпляра на разных фото').fill('chair-one')
            self.draw(panel)
            panel.get_by_role('button', name='Подтверждаю предмет и связь', exact=True).click()
            expected_revision = str(int(photo)+1)
            self.page.wait_for_function("n=>document.querySelector('#phonePhotoInventory details[data-instance-revision=\"'+n+'\"]')!==null", arg=expected_revision)
            panel.get_by_text('Сохранено.', exact=False).wait_for()
        self.assertEqual(self.review()['summary']['confirmedVisibleCounts'], {'chairs': 1})
        self.assertEqual(len(self.review()['document']['observations']), 2)
        panel.get_by_role('button', name='Подтвердить видимое количество: Стулья — 1', exact=True).click()
        self.page.wait_for_function("document.querySelector('#phonePhotoInventory details[data-instance-revision=\"3\"]')!==null")
        panel.get_by_text('Количество подтверждено', exact=False).wait_for()
        self.assertEqual(self.review()['summary']['manuallyConfirmedVisibleCounts'], {'chairs': 1})
        panel.get_by_label('Исходное фото для сопоставления').select_option('0')
        panel.get_by_label('Вид предмета').select_option('chairs')
        panel.get_by_label('ID того же экземпляра на разных фото').fill('chair-one')
        self.draw(panel, (.65, .2), (.9, .6))
        panel.get_by_role('button', name='Подтверждаю предмет и связь', exact=True).click()
        panel.get_by_text('Разные области одного фото ошибочно объединены', exact=False).wait_for()
        summary = self.review()['summary']
        self.assertEqual(summary['manuallyConfirmedVisibleCounts'], {})
        self.assertEqual(summary['confirmedVisibleCounts'], {})
        self.assertFalse(summary['apartmentTotalExact'])
        fixture.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_viewer_cannot_open_instance_api_or_edit_links(self):
        fixture.PhoneBrowserAcceptance.connect(self)
        fixture.grant_paired_role(self, 'viewer')
        self.page.locator('#phoneCards .listing-card').first.click()
        self.assertEqual(self.page.get_by_text('Экземпляры мебели · сопоставить ракурсы', exact=True).count(), 0)
        response = self.context.request.get(self.base+'/api/phone/photo-instances/'+self.card_id)
        self.assertEqual(response.status, 403)


if __name__ == '__main__':
    unittest.main()
