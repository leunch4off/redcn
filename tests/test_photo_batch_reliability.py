"""An incomplete/cropping-failed item cannot collapse a 19-photo source album."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay
import photo_store
import redcn
from PIL import Image


def picture(index):
    stream=io.BytesIO();Image.new('RGB',(480,360),(index*11%256,index*7%256,index*13%256)).save(stream,'PNG')
    return stream.getvalue()


class PhotoBatchReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.urls=['https://images.cdn-cian.ru/images/batch-'+str(index)+'.png' for index in range(19)]

    def tearDown(self):self.temp.cleanup()

    def test_nineteen_sources_download_crop_failures_and_retry_keep_all_other_originals(self):
        result={'fields':{'photos':[self.urls[0]]},'listing':{'images':self.urls},'titles':['真实房源'],'body':'正文','tags':'#莫斯科租房'}
        fetched=[];first=True;crop_failed=False
        def fetch(url,**kwargs):
            fetched.append(url)
            if first and url==self.urls[6]:raise RuntimeError('fixture image transport failed')
            return picture(self.urls.index(url)),'image/png'
        def crop(directory,digest,**kwargs):
            nonlocal crop_failed
            if not crop_failed and photo_store.asset(directory,digest)[2]['sourceUrl']==self.urls[2]:
                crop_failed=True;raise RuntimeError('fixture crop worker failed')
            return {'autoStatus':'unchanged'}
        with patch.object(redcn,'fetch_bytes',side_effect=fetch),patch.object(photo_store,'auto_crop_photo',side_effect=crop):
            phone_relay.cache_photos(self.directory,result)
            self.assertEqual(len(result['media']),19)
            self.assertEqual(result['mediaStatus'],'partial')
            self.assertEqual(result['photoProcessing']['downloaded'],18)
            self.assertEqual(result['photoProcessing']['downloadFailed'],1)
            self.assertEqual(result['photoProcessing']['failed'],1)
            self.assertEqual(result['photoProcessing']['unchanged'],17)
            self.assertEqual([(row['index'],row['stage'],row['originalSaved']) for row in result['mediaFailures']],[(3,'crop',True),(7,'download',False)])
            identifier=local_server.save_result(self.directory,result)
            card=phone_relay.public_card(self.directory,identifier)
            self.assertEqual(card['photoCount'],19);self.assertEqual(len(card['media']),18)
            self.assertEqual(len(card['mediaFailures']),2)
            for variant in ('original','prepared'):
                archive=zipfile.ZipFile(io.BytesIO(phone_relay.card_zip(self.directory,identifier,variant)))
                manifest=json.loads(archive.read('manifest.json'))
                self.assertEqual(len(manifest),18);self.assertEqual({row['kind'] for row in manifest},{'original'})
                self.assertIn(self.urls[2],[row['sourceUrl'] for row in manifest])
            first=False;fetched.clear()
            phone_relay.cache_photos(self.directory,result)
        self.assertEqual(fetched,[self.urls[6]],'Retry reuses every previously saved original')
        self.assertEqual(result['mediaStatus'],'ready');self.assertEqual(result['photoProcessing']['downloaded'],19)
        self.assertEqual(result['mediaFailures'],[])
        self.assertEqual(len(phone_relay.card_media(self.directory,result)),19)

    def test_union_preserves_operator_order_exclusions_and_layout_filter(self):
        result={'fields':{'photos':[self.urls[0]],'photoEdits':{'order':[self.urls[18],self.urls[0]],'excluded':[self.urls[3]]}},
            'listing':{'images':self.urls,'photoItems':[{'url':self.urls[4],'isLayout':True}]}}
        urls=phone_relay.source_images(result)
        self.assertEqual(urls[:2],[self.urls[18],self.urls[0]])
        self.assertEqual(len(urls),17);self.assertNotIn(self.urls[3],urls);self.assertNotIn(self.urls[4],urls)

    def test_cli_crop_failure_keeps_original_path_and_continues_nineteen_entries(self):
        def fetch(url,**kwargs):
            if url==self.urls[6]:raise RuntimeError('fixture download failed')
            return picture(self.urls.index(url)),'image/png'
        def crop(directory,digest,*args,**kwargs):
            if photo_store.asset(directory,digest)[2]['sourceUrl']==self.urls[2]:raise RuntimeError('fixture crop failed')
            return {'status':'review','autoStatus':'unchanged'}
        with patch.object(redcn,'fetch_bytes',side_effect=fetch),patch.object(photo_store,'crop_photo',side_effect=crop):
            rows=redcn.download_photos(self.urls,self.directory/'export',crop=True)
        self.assertEqual(len(rows),19);self.assertEqual(sum('path' in row for row in rows),18)
        self.assertTrue(Path(rows[2]['path']).is_file());self.assertNotIn('error',rows[2]);self.assertEqual(rows[2]['crop']['errorClass'],'RuntimeError')
        self.assertIn('error',rows[6]);self.assertTrue(Path(rows[18]['path']).is_file())
        self.assertEqual(json.loads((self.directory/'export'/'manifest.json').read_text()),rows)

    def test_zip_reports_one_corrupt_original_and_exports_the_remaining_eighteen(self):
        result={'fields':{'photos':self.urls},'titles':['真实房源'],'body':'正文','tags':'#莫斯科租房'}
        with patch.object(redcn,'fetch_bytes',side_effect=lambda url,**kwargs:(picture(self.urls.index(url)),'image/png')),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            phone_relay.cache_photos(self.directory,result)
        identifier=local_server.save_result(self.directory,result)
        broken=result['media'][3]['id'];(self.directory/'media'/broken).write_bytes(b'fixture damaged file')
        archive=zipfile.ZipFile(io.BytesIO(phone_relay.card_zip(self.directory,identifier,'original')))
        manifest=json.loads(archive.read('manifest.json'))
        self.assertEqual(len(manifest),19);self.assertEqual(sum('name' in row for row in manifest),18)
        self.assertEqual(manifest[3]['stage'],'export');self.assertEqual(manifest[3]['sourceUrl'],self.urls[3]);self.assertIn('error',manifest[3])


if __name__=='__main__':unittest.main()
