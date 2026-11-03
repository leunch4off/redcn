"""Desktop review must load originals through authenticated binary requests."""
import base64
import io
from pathlib import Path
import unittest

import test_ui_browser as fixture
import local_server
import photo_instances
import photo_store


class DesktopPhotoInstanceEditorTests(unittest.TestCase):
    setUpClass = classmethod(fixture.BrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(fixture.BrowserAcceptance.tearDownClass.__func__)
    setUp = fixture.BrowserAcceptance.setUp
    tearDown = fixture.BrowserAcceptance.tearDown

    def test_owner_can_see_original_and_save_a_visible_instance_without_anonymous_media(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (640, 420), '#7b968f').save(stream, 'PNG')
        url = 'https://images.cdn-cian.ru/images/desktop-instance-fixture.jpg'
        photo = photo_store.attach_photo(self.storage.name, url,
            'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())
        card_id = local_server.save_result(Path(self.storage.name), {
            'fields': {'price': '80000', 'photos': [url]},
            'titles': ['合成家具测试'], 'body': '合成测试。', 'tags': '#测试'})
        self.page.add_init_script("localStorage.setItem('most-photo-autoload','false')")
        media_requests = []
        self.page.on('request', lambda request: media_requests.append(request)
            if '/api/local/media/' in request.url else None)
        self.page.goto(self.server.desktop_access.launch_url(self.server.server_port, card_id))
        panel = self.page.locator('details.photo-instance-editor')
        panel.locator('summary').click()
        image = panel.locator('img')
        image.wait_for(state='visible')
        self.page.wait_for_function("document.querySelector('.photo-instance-editor img')?.naturalWidth === 640")
        image.evaluate('node=>node.decode()')
        self.assertTrue(image.get_attribute('src').startswith('blob:'))
        self.assertGreaterEqual(len(media_requests), 1)
        self.assertTrue(all(request.headers.get('x-redcn-token') == self.server.desktop_access.token
                            for request in media_requests))
        panel.get_by_label('Вид предмета').select_option('chairs')
        panel.get_by_label('ID того же экземпляра на разных фото').fill('chair-one')
        image.scroll_into_view_if_needed()
        bounds = image.bounding_box()
        self.page.mouse.move(bounds['x'] + bounds['width'] * .1, bounds['y'] + bounds['height'] * .1)
        self.page.mouse.down()
        self.page.mouse.move(bounds['x'] + bounds['width'] * .4, bounds['y'] + bounds['height'] * .6, steps=3)
        self.page.mouse.up()
        panel.get_by_role('button', name='Подтверждаю предмет и связь', exact=True).click()
        self.page.wait_for_function("document.querySelector('.photo-instance-editor')?.dataset.instanceRevision === '1'")
        saved = photo_instances.load_review(self.storage.name, card_id, photo_ids=[photo['id']])
        self.assertEqual(saved['summary']['confirmedVisibleCounts'], {'chairs': 1})
        self.assertEqual(local_server.read_result(Path(self.storage.name), card_id)['body'], '合成测试。')


if __name__ == '__main__':
    unittest.main()
