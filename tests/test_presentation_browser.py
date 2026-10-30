"""Mac Chrome acceptance of reusable designs and private mobile dossiers."""
import base64
import json
from pathlib import Path
import threading
import unittest

import test_phone_browser as acceptance


class PresentationAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=acceptance.PhoneBrowserAcceptance.setUp
    tearDown=acceptance.PhoneBrowserAcceptance.tearDown

    def take_download(self,selector,timeout=30000):
        try:
            with self.page.expect_download(timeout=timeout) as event:self.page.locator(selector).click()
            return event.value
        except Exception as error:
            self.fail(selector+': '+self.page.locator('#presentationStatus').inner_text()+'; '+str(error))

    def test_desktop_template_exports_and_phone_dossier_roundtrip(self):
        thread=threading.Thread(target=self.controller.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(self.controller.server_port)
        try:
            self.page.set_viewport_size({'width':1440,'height':1100})
            self.page.goto(base+'/listing/'+self.card_id)
            self.page.wait_for_function("document.getElementById('output').value.includes('两间独立')")
            self.page.locator('#openPresentation').click()
            self.page.wait_for_function("document.getElementById('presentationStatus').textContent.includes('2 фото')")
            self.page.locator('#presentation-contact').fill('WeChat: MOST-TEST')
            self.page.locator('#presentation-contact').dispatch_event('change')
            self.page.locator('#presentationSave').click()
            self.page.wait_for_function("document.getElementById('presentationStatus').textContent.includes('сохранён на Mac')")
            try:
                with self.page.expect_download(timeout=15000) as output:self.page.locator('#presentationHtml').click()
            except Exception as error:
                self.fail('HTML export: '+self.page.locator('#presentationStatus').inner_text()+'; '+str(error))
            output.value.save_as('/tmp/redcn-v10-acceptance.html')
            self.assertIn('MOST-TEST',Path('/tmp/redcn-v10-acceptance.html').read_text())
            self.assertNotIn('redcn-token',Path('/tmp/redcn-v10-acceptance.html').read_text())
            self.assertNotIn('<base',Path('/tmp/redcn-v10-acceptance.html').read_text())
            self.page.locator('#presentationTabintro').click()
            self.page.wait_for_function("document.getElementById('presentationImagePreview').naturalWidth===1080")
            self.take_download('#presentationImageDownload',15000).save_as('/tmp/redcn-v10-intro.png')
            self.page.locator('#presentationTaboutro').click()
            self.take_download('#presentationImageDownload',15000).save_as('/tmp/redcn-v10-outro.png')
            self.page.locator('#presentationTabpdf').click()
            self.take_download('#presentationPdf',90000).save_as('/tmp/redcn-v10-acceptance.pdf')
            self.assertTrue(Path('/tmp/redcn-v10-acceptance.pdf').read_bytes().startswith(b'%PDF-'))
            self.page.locator('#presentationProof').click()
            self.page.locator('#presentationProofDialog').wait_for(state='visible',timeout=90000)
            self.assertTrue(self.page.locator('#presentationProofFrame').get_attribute('src').startswith('blob:'))
            self.page.locator('#presentationProofClose').click()
            self.assertFalse(self.page.locator('#presentationProofFrame').get_attribute('src'))
            self.page.screenshot(path='/tmp/redcn-v10-design-desktop.png',full_page=True)
            self.page.locator('nav [data-tab="editor"]').click()
            archive=self.page.locator('#cardArchiveMount')
            archive.get_by_label('Статус квартиры',exact=True).select_option('rented')
            archive.get_by_label('Личные заметки квартиры').fill('Частная заметка для следующего клиента')
            archive.get_by_role('button',name='Сохранить статус и заметки',exact=True).click()
            self.page.wait_for_function("document.querySelector('#cardArchiveMount .archive-status').textContent.includes('сохран')")
            archive.get_by_label('Файл для архива квартиры').set_input_files({'name':'Договор.txt','mimeType':'text/plain','buffer':b'PRIVATE-CONTRACT-TEST'})
            archive.get_by_role('button',name='Прикрепить файл',exact=True).click()
            archive.get_by_role('button',name='Скачать Договор.txt',exact=True).wait_for()
            self.page.reload()
            self.page.locator('#cardArchiveMount').get_by_role('button',name='Скачать Договор.txt',exact=True).wait_for()
            self.assertEqual(self.page.locator('#cardArchiveMount').get_by_label('Статус квартиры',exact=True).input_value(),'rented')
            token=self.page.locator('meta[name="redcn-token"]').get_attribute('content')
            saved=self.context.request.get(base+'/api/local/card-exports/'+self.card_id,headers={'X-Redcn-Token':token}).json()['exports']
            self.assertEqual({item['kind'] for item in saved},{'html','pdf','intro','outro'})
            # Phone receives all marketing exports and the private dossier only after pairing.
            code,_=self.phone.pairing.issue();self.page.set_viewport_size({'width':393,'height':852})
            self.page.goto(self.base+'/phone/pair#'+code)
            self.page.locator('#phoneCards .listing-card').first.click()
            phone_archive=self.page.locator('#phoneArchiveMount')
            phone_archive.get_by_role('button',name='Скачать Договор.txt',exact=True).wait_for()
            self.assertEqual(phone_archive.get_by_label('Личные заметки квартиры').input_value(),'Частная заметка для следующего клиента')
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
            files=self.page.locator('#phoneFilesMount')
            files.get_by_role('button',name='PDF',exact=True).first.wait_for()
            with self.page.expect_download() as output:files.get_by_role('button',name='PDF',exact=True).first.click()
            self.assertTrue(output.value.suggested_filename.endswith('.pdf'))
            with self.page.expect_download() as output:phone_archive.get_by_role('button',name='Скачать Договор.txt',exact=True).click()
            self.assertEqual(Path(output.value.path()).read_bytes(),b'PRIVATE-CONTRACT-TEST')
            self.page.locator('#phoneDesignCard').click()
            self.page.locator('#presentation-contact').wait_for(state='visible')
            self.assertEqual(self.page.locator('#presentation-contact').input_value(),'WeChat: MOST-TEST')
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
            self.page.screenshot(path='/tmp/redcn-v10-design-phone.png',full_page=True)
        finally:
            self.controller.shutdown();thread.join()

    def test_new_card_first_export_keeps_stable_id_in_draft(self):
        thread=threading.Thread(target=self.controller.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(self.controller.server_port)
        try:
            self.page.goto(base+'/');self.page.locator('#price').fill('92000');self.page.locator('#area').fill('46');self.page.locator('#metro').fill('Прокшино');self.page.locator('#generate').click()
            self.page.locator('#openPresentation').click()
            try:
                with self.page.expect_download(timeout=15000) as download:self.page.locator('#presentationHtml').click()
            except Exception as error:
                self.fail('New card HTML export: '+self.page.locator('#presentationStatus').inner_text()+'; '+str(error))
            self.assertTrue(download.value.suggested_filename.endswith('.html'))
            draft=self.page.evaluate("JSON.parse(localStorage.getItem('most-draft'))")
            result_id=draft['fields']['resultId'];self.assertRegex(result_id,r'^[a-f0-9]{32}$')
            self.assertEqual(self.page.url,base+'/listing/'+result_id)
            self.page.goto(base+'/')
            self.assertTrue(self.page.locator('#resultLink').get_attribute('href').endswith(result_id))
        finally:
            self.controller.shutdown();thread.join()


if __name__=='__main__':unittest.main()
