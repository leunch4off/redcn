"""Actual Chrome operator workflow on isolated synthetic cards and images."""
import base64
import io
import json
from pathlib import Path
import unittest

import test_ui_browser as fixture
import local_server
import photo_store
import photo_training


class PhotoTrainingBrowserTests(unittest.TestCase):
    setUpClass=classmethod(fixture.BrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(fixture.BrowserAcceptance.tearDownClass.__func__)
    setUp=fixture.BrowserAcceptance.setUp
    tearDown=fixture.BrowserAcceptance.tearDown

    def test_draw_verify_and_export_only_human_labels_without_changing_listing(self):
        from PIL import Image
        stream=io.BytesIO();Image.new('RGB',(640,420),'#77919b').save(stream,'PNG')
        url='https://images.cdn-cian.ru/images/photo-training-browser-fictional.jpg'
        photo=photo_store.attach_photo(self.storage.name,url,'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())
        card=local_server.save_result(Path(self.storage.name),{'fields':{'price':'85000','photos':[url]},
            'titles':['合成照片标注测试'],'body':'合成测试，请勿发布。','tags':'#测试'})
        self.page.add_init_script("localStorage.setItem('most-photo-autoload','false');localStorage.setItem('most-photo-analyze','false')")
        self.page.goto(self.server.desktop_access.launch_url(self.server.server_port,card))
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
        panel=self.page.locator('details.photo-training-studio')
        section=panel.evaluate("node=>node.closest('main>section')?.id")
        if section:self.page.locator('nav [data-tab="'+section+'"]').click()
        panel.locator(':scope > summary').click()
        panel.get_by_role('button',name='Загрузить разметку карточки',exact=True).click()
        image=panel.locator('img');image.wait_for(state='visible')
        self.page.wait_for_function("document.querySelector('.photo-training-studio img')?.naturalWidth===640")
        panel.get_by_label('Общая группа квартиры',exact=True).fill('synthetic-browser-group')
        panel.get_by_label('Часть корпуса',exact=True).select_option('train')
        panel.get_by_label('Разрешаю использовать эти оригиналы для обучения',exact=True).check()
        panel.get_by_label('Основание использования',exact=True).select_option('owner-authorized')
        panel.get_by_label('Ракурсы, кропы и повторные объявления сверены; одна квартира находится в одной группе',exact=True).check()
        panel.get_by_label('Категория предмета',exact=True).select_option('sofa')
        image.scroll_into_view_if_needed();bounds=image.bounding_box()
        self.page.mouse.move(bounds['x']+bounds['width']*.1,bounds['y']+bounds['height']*.1)
        self.page.mouse.down();self.page.mouse.move(bounds['x']+bounds['width']*.5,bounds['y']+bounds['height']*.5,steps=3);self.page.mouse.up()
        self.assertEqual(panel.get_by_label('Решение',exact=True).input_value(),'present')
        self.assertFalse(panel.get_by_label('Подтверждаю метку для обучения',exact=True).is_checked())
        panel.get_by_label('Проверил всю эту категорию на данном фото',exact=True).check()
        panel.get_by_label('Подтверждаю метку для обучения',exact=True).check()
        panel.get_by_role('button',name='Сохранить решение и параметры',exact=True).click()
        self.page.wait_for_function("document.querySelector('.photo-training-studio')?.dataset.reviewRevision==='1'")
        saved=photo_training.view(self.storage.name,card)
        self.assertEqual(saved['document']['rows'][0]['state'],'present')
        self.assertTrue(saved['document']['rows'][0]['verified'])
        self.assertEqual(saved['document']['rows'][0]['photoId'],photo['id'])
        self.assertEqual(saved['document']['rights']['photoIds'],[photo['id']])
        with self.page.expect_download() as requested:
            panel.get_by_role('button',name='Скачать проверенные метки этой карточки',exact=True).click()
        download=requested.value
        self.assertEqual(download.suggested_filename,'redcn-private-photo-labels.json')
        exported=json.loads(Path(download.path()).read_text())
        self.assertEqual(len(exported['splits']['train']),1)
        self.assertFalse(exported['automaticAcceptance'])
        self.assertEqual(exported,photo_training.export_manifest(self.storage.name,[card]))
        self.assertEqual(local_server.read_result(Path(self.storage.name),card)['body'],'合成测试，请勿发布。')


if __name__=='__main__':unittest.main()
