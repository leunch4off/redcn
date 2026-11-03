"""Saved human decisions follow the canonical original, not a stale IDB bitmap.

All photographs and decisions below are disposable synthetic browser fixtures.
No model, remote listing, or human-labelled training data is used.
"""
import base64
import hashlib
import io
from pathlib import Path
import re
import time
import unittest
from unittest.mock import patch

import test_phone_browser as phone_fixture
import test_photo_inventory_browser as inventory_fixture


class PhotoCacheDecisionsBrowserTests(unittest.TestCase):
    setUpClass=classmethod(phone_fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(phone_fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    tearDown=inventory_fixture.PhotoInventoryBrowserAcceptance.tearDown
    open_desktop=inventory_fixture.PhotoInventoryBrowserAcceptance.open_desktop

    @staticmethod
    def png(color):
        from PIL import Image
        stream=io.BytesIO();Image.new('RGB',(640,420),color).save(stream,'PNG')
        return stream.getvalue()

    def setUp(self):
        inventory_fixture.PhotoInventoryBrowserAcceptance.setUp(self)
        self.url=self.assets[0]['sourceUrl'];self.saved_sha=self.assets[0]['id']
        self.result['fields'].update({'photos':[self.url],'features':['fridge'],
            'featureCounts':{'fridge':1},'photoEdits':{'observations':{self.url:{
                'analysis':inventory_fixture.observation(self.saved_sha,['fridge','sofa']),
                'confirmed':{'fridge':{'count':1}},'rejected':{'sofa':True}}}}})
        phone_fixture.local_server.save_result(self.directory,self.result,self.card_id)
        self.stale_bytes=self.png('#53bd71')
        self.stale_sha=hashlib.sha256(self.stale_bytes).hexdigest()
        self.assertNotEqual(self.saved_sha,self.stale_sha)

    def hydrate_stale_cache(self):
        self.open_desktop()
        self.page.evaluate("""async ({url,sha,base64}) => {
            const db=await new Promise((resolve,reject)=>{
                const request=indexedDB.open('redcn-photos-v1',1);
                request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
            });
            const bytes=Uint8Array.from(atob(base64),character=>character.charCodeAt(0));
            await new Promise((resolve,reject)=>{
                const tx=db.transaction('photos','readwrite');
                tx.objectStore('photos').put({url,originalUrl:url,cacheVersion:2,
                    blob:new Blob([bytes],{type:'image/png'}),width:640,height:420,
                    mime:'image/png',serverId:sha,analysis:{originalId:sha,candidates:[]},at:1});
                tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);
            });
            db.close();
        }""",{'url':self.url,'sha':self.stale_sha,'base64':base64.b64encode(self.stale_bytes).decode()})
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true' && document.getElementById('photoStatus').textContent.startsWith('1 / 1 оригиналов загружено')")

    def save_card(self):
        with self.page.expect_response(lambda response:response.url.endswith('/api/local/save') and response.request.method=='POST') as response:
            self.page.locator('#saveLocal').click()
        self.assertEqual(response.value.status,200)
        self.page.wait_for_function("document.getElementById('localSaveStatus').textContent.includes('Сохранено')")
        return phone_fixture.local_server.read_result(self.directory,self.card_id)

    def cached_sha(self):
        return self.page.evaluate("""async url => {
            const db=await new Promise((resolve,reject)=>{
                const request=indexedDB.open('redcn-photos-v1',1);
                request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
            });
            const value=await new Promise((resolve,reject)=>{
                const request=db.transaction('photos').objectStore('photos').get(url);
                request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
            });db.close();return value?.serverId;
        }""",self.url)

    def test_stale_bitmap_cannot_erase_saved_decisions_for_unchanged_canonical_original(self):
        self.hydrate_stale_cache()
        self.assertEqual(self.cached_sha(),self.saved_sha)
        fridge=self.page.locator('#photoObservations label').filter(has_text='Холодильник')
        sofa=self.page.locator('#photoObservations label').filter(has_text='Диван')
        self.assertEqual(fridge.count(),1,'The saved fridge observation must survive stale cache hydration')
        self.assertEqual(sofa.count(),1,'The saved rejected observation must remain reviewable')
        self.assertTrue(fridge.locator('input[type="checkbox"]').is_checked())
        self.assertEqual(fridge.locator('input[type="number"]').input_value(),'1')
        self.assertFalse(sofa.locator('input[type="checkbox"]').is_checked())
        saved=self.save_card()['fields']['photoEdits']['observations'][self.url]
        self.assertEqual(saved['analysis']['originalId'],self.saved_sha)
        self.assertEqual(saved['confirmed'],{'fridge':{'count':1}})
        self.assertEqual(saved['rejected'],{'sofa':True})

    def test_actual_canonical_replacement_does_not_transfer_old_human_decisions(self):
        replacement=self.png('#5a4ad8')
        current=phone_fixture.photo_store.attach_photo(self.directory,self.url,
            'data:image/png;base64,'+base64.b64encode(replacement).decode())
        self.assertNotIn(current['id'],[self.saved_sha,self.stale_sha])
        self.hydrate_stale_cache()
        self.assertEqual(self.cached_sha(),current['id'])
        self.assertEqual(self.page.locator('#photoObservations input[type="checkbox"]').count(),0)
        saved=self.save_card()
        self.assertNotIn(self.url,saved['fields']['photoEdits']['observations'])

    def test_same_original_reanalysis_keeps_only_explicit_human_decisions(self):
        previous=inventory_fixture.observation(self.saved_sha,['fridge','sofa','bedAny'])
        previous['autoApply']=False
        self.result['fields']['photoEdits']['observations'][self.url]['analysis']=previous
        phone_fixture.local_server.save_result(self.directory,self.result,self.card_id)
        self.open_desktop()
        self.assertEqual(self.page.locator('#photoObservations label').count(),3)
        empty=inventory_fixture.observation(self.saved_sha,[])
        empty['unknownIds']=['fridge','sofa','bedAny']
        with patch.object(phone_fixture.photo_store,'analyze_photo',return_value=empty) as model:
            self.page.locator('#photoAnalyze').click()
            self.page.wait_for_function("!document.getElementById('photoAnalyze').disabled && document.getElementById('photoStatus').textContent.startsWith('1 / 1 оригиналов загружено')")
            self.assertEqual(model.call_count,1)
        fridge=self.page.locator('#photoObservations label').filter(has_text='Холодильник')
        sofa=self.page.locator('#photoObservations label').filter(has_text='Диван')
        self.assertEqual(fridge.count(),1)
        self.assertEqual(sofa.count(),1)
        self.assertEqual(self.page.locator('#photoObservations label').count(),2,
            'Only human-reviewed rows survive; an old model-only guess must disappear')
        self.assertTrue(fridge.locator('input[type="checkbox"]').is_checked())
        self.assertEqual(fridge.locator('input[type="number"]').input_value(),'1')
        self.assertFalse(sofa.locator('input[type="checkbox"]').is_checked())
        self.assertTrue(self.page.locator('#feature-fridge').is_checked())
        self.assertFalse(self.page.locator('#feature-sofa').is_checked())
        self.assertFalse(self.page.locator('#feature-bedAny').is_checked())
        saved=self.save_card();fields=saved['fields']
        row=fields['photoEdits']['observations'][self.url]
        self.assertEqual(row['analysis']['originalId'],self.saved_sha)
        self.assertEqual({item['id'] for item in row['analysis']['candidates']},{'fridge','sofa'})
        self.assertEqual(row['confirmed'],{'fridge':{'count':1}})
        self.assertEqual(row['rejected'],{'sofa':True})
        self.assertIn('fridge',fields['features'])
        self.assertNotIn('sofa',fields['features']);self.assertNotIn('bedAny',fields['features'])
        self.assertEqual(fields['featureCounts']['fridge'],1)
        self.assertTrue(any(item['id']=='fridge' and item['status']=='confirmed'
            and item['originalId']==self.saved_sha for item in fields['photoEvidence']))
        self.assertEqual(saved['body'],self.result['body'])

    def test_export_revalidates_ready_original_inside_five_second_display_cache(self):
        # This card has no photo decisions to invalidate. Export should render
        # the current canonical bytes even while the display cache is fresh.
        # Freeze the clock used by the display TTL, not request/render timers:
        # a slow machine must not expire the cache while creating the first HTML.
        fixed_time=int(time.time())
        self.page.clock.set_fixed_time(fixed_time)
        self.result['fields'].pop('photoEdits')
        self.result['fields'].update(features=[],featureCounts={})
        phone_fixture.local_server.save_result(self.directory,self.result,self.card_id)
        accepted=[];media_reads=[]
        def request_seen(request):
            if request.method=='POST' and request.url.endswith('/api/local/photo'):
                if request.post_data_json.get('action')=='accepted':accepted.append(request.url)
            if request.method=='GET' and '/api/local/media/' in request.url:
                media_reads.append(request.url.rsplit('/',1)[-1].split('?',1)[0])
        self.page.on('request',request_seen)
        self.open_desktop();self.page.locator('#openPresentation').click()
        self.page.wait_for_function("document.getElementById('presentationMount').dataset.photosReady==='true'")
        with self.page.expect_download(timeout=15000) as before:
            self.page.locator('#presentationHtml').click()
        initial_html=Path(before.value.path()).read_text()
        self.assertEqual(self.cached_sha(),self.saved_sha)
        self.assertTrue(accepted)
        previous_count=len(accepted)

        replacement=self.png('#dc5c9d')
        current=phone_fixture.photo_store.attach_photo(self.directory,self.url,
            'data:image/png;base64,'+base64.b64encode(replacement).decode())
        self.assertNotEqual(current['id'],self.saved_sha)
        self.assertEqual(self.page.evaluate('Date.now()'),fixed_time*1000,
            'The display cache clock must remain fixed while replacing the original')
        media_reads.clear()
        with self.page.expect_download(timeout=15000) as after:
            self.page.locator('#presentationHtml').click()
        changed_html=Path(after.value.path()).read_text()
        self.assertGreater(len(accepted),previous_count,'Export must request canonical metadata again')
        self.assertEqual(self.page.evaluate('Date.now()'),fixed_time*1000,
            'The new metadata read must bypass a display cache whose TTL has not advanced')
        self.assertIn(current['id'],media_reads)
        self.assertEqual(self.cached_sha(),current['id'])

        def photo_colours(html):
            from PIL import Image
            result=[]
            for encoded in re.findall(r'data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=]+)',html):
                image=Image.open(io.BytesIO(base64.b64decode(encoded))).convert('RGB')
                if image.width>=320 and image.height>=200:
                    result.append(image.getpixel((image.width//2,image.height//2)))
            return result
        near=lambda actual,expected:max(abs(a-b) for a,b in zip(actual,expected))<=4
        old_colour=(0xd5,0xbb,0xa4);new_colour=(0xdc,0x5c,0x9d)
        self.assertTrue(any(near(colour,old_colour) for colour in photo_colours(initial_html)),
            'The first client HTML must contain the initial fixture photograph')
        self.assertTrue(any(near(colour,new_colour) for colour in photo_colours(changed_html)),
            'The second client HTML must embed the replacement photograph, not only a fresh receipt')
        self.assertFalse(any(near(colour,old_colour) for colour in photo_colours(changed_html)),
            'The superseded photograph must not remain in the new client HTML')


if __name__=='__main__':unittest.main()
