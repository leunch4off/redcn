"""Real prepare jobs preserve prose until changed facts yield accepted model copy.

Only the external photo detector, image download and model preview are fixtures.
The photo revision, Node fact resolver, job lifecycle, CAS and catalogue writes
run normally against disposable SQLite databases.
"""
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_catalog
import listing_photo_inventory
import local_content
import local_server
import local_text_model
import photo_ai_settings
import photo_store
import phone_relay
import redcn
import test_photo_generation as fixtures


MODEL_DRAFT={'status':'review','title':'照片中的家具，让这套两居更具体',
    'body':'这套54㎡两居月租80000卢布。照片中可见冰箱、沙发和床。',
    'tags':'#两居室 #莫斯科租房','fallback':False,'modelAccepted':True,
    'generationMode':'individual','textSource':'local-model'}


class PrepareIndividualIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.environment=patch.dict(os.environ,{'REDCN_VISION_MODEL':'','REDCN_VISION_ENDPOINT':''})
        self.environment.start()
        self.server=local_server.create_server(self.directory,0)
        photo_ai_settings.save_config(self.directory,{'enabled':True,'endpoint':'http://127.0.0.1:11435'})
        self.old_id,self.new_id='a'*32,'b'*32
        self.original=fixtures.card()
        self.original['body']='这是编辑已经写好的独立描述，不能退回固定句式。'
        with patch('local_server.time.time',return_value=100):
            local_server.save_result(self.directory,self.original,self.old_id,kind='import')
        with patch('local_server.time.time',return_value=200):
            local_server.save_result(self.directory,fixtures.card(),self.new_id,kind='import')

    def tearDown(self):
        self.server.server_close();self.environment.stop();self.temp.cleanup()

    def catalogue(self):
        with redcn.open_cache(self.directory) as db:
            rows=card_catalog.summaries(db,[self.old_id,self.new_id])
        return rows

    def run_prepare(self,*,draft=None,enabled=True,settings_error=None,observed=None):
        observed=fixtures.inventory() if observed is None else observed
        def photos(directory,result,report=None,cancel=None):
            result['mediaStatus']='ready'
        with patch.object(photo_store,'pillow',return_value=None), \
                patch.object(phone_relay,'cache_photos',side_effect=photos), \
                patch.object(listing_photo_inventory,'analyze_listing_photos',side_effect=lambda *a,**k:copy.deepcopy(observed)), \
                patch.object(local_text_model,'settings',side_effect=settings_error,return_value={'advertisingEnabled':enabled}), \
                patch.object(local_content,'generate_preview',side_effect=draft if callable(draft) else None,
                    return_value=copy.deepcopy(MODEL_DRAFT if draft is None else draft)) as model:
            previous=set(threading.enumerate())
            job_id=self.server.prepare_card(self.old_id,analyze=True)
            workers=[worker for worker in threading.enumerate() if worker not in previous]
            try:
                until=time.monotonic()+8
                while time.monotonic()<until:
                    job=self.server.job_status(job_id)
                    if job['state']!='running':break
                    time.sleep(.01)
                else:self.fail('Isolated prepare job timed out')
            finally:
                for worker in workers:worker.join(3)
            self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(job['state'],'done',job)
        return local_server.read_result(self.directory,self.old_id),model

    def test_changed_facts_use_individual_model_and_mark_edit_without_moving_card(self):
        before=self.catalogue()
        saved,model=self.run_prepare()
        model.assert_called_once()
        request=model.call_args.args[1]
        self.assertEqual(request['styleMode'],'individual')
        self.assertEqual(request['output']['body'],self.original['body'])
        self.assertTrue({'fridge','sofa','bedAny'}<=set(request['fields']['features']))
        self.assertEqual(saved['body'],MODEL_DRAFT['body'])
        self.assertEqual(saved['titles'],[MODEL_DRAFT['title']])
        self.assertEqual(saved['tags'],MODEL_DRAFT['tags'])
        self.assertTrue(saved['photoInventory']['textUpdated'])
        after=self.catalogue()
        self.assertTrue(after[self.old_id]['contentEdited'])
        self.assertEqual([after[key]['catalogOrder'] for key in (self.old_id,self.new_id)],
                         [before[key]['catalogOrder'] for key in (self.old_id,self.new_id)])
        self.assertEqual(sorted(after,key=lambda key:-after[key]['catalogOrder']),[self.new_id,self.old_id])

    def test_rejected_and_unavailable_model_preserve_text_but_save_photo_evidence(self):
        for state in ('blocked','unavailable'):
            with self.subTest(state=state):
                # A separate card keeps each branch a first accepted observation.
                local_server.save_result(self.directory,self.original,self.old_id,kind='import')
                saved,model=self.run_prepare(draft={'status':state,'warnings':['Isolated draft rejected']})
                model.assert_called_once()
                for key in ('body','titles','tags'):
                    self.assertEqual(saved[key],self.original[key])
                self.assertTrue(saved['photoInventory']['photoEvidence'])
                self.assertTrue(saved['fields']['photoEvidence'])
                self.assertFalse(saved['photoInventory']['textUpdated'])
                self.assertEqual(saved['aiAdvertisement']['status'],state)
                self.assertFalse(self.catalogue()[self.old_id]['contentEdited'])

    def test_disabled_or_broken_settings_preserve_individual_copy(self):
        for enabled,error in ((False,None),(True,ValueError('isolated invalid settings'))):
            with self.subTest(enabled=enabled,error=error):
                local_server.save_result(self.directory,self.original,self.old_id,kind='import')
                saved,model=self.run_prepare(enabled=enabled,settings_error=error)
                model.assert_not_called()
                for key in ('body','titles','tags'):
                    self.assertEqual(saved[key],self.original[key])
                self.assertTrue(saved['fields']['photoEvidence'])
                self.assertFalse(self.catalogue()[self.old_id]['contentEdited'])

    def test_repeated_same_facts_refresh_observations_without_another_model_call(self):
        first,_=self.run_prepare()
        before=self.catalogue()[self.old_id]
        observed=fixtures.inventory()
        for row in observed['photoEvidence']:
            row['confidence']=.99
            row['evidence'][0]['confidence']=.99
            row['evidence'][0]['text']+=' Повторная проверка того же снимка.'
        repeated,model=self.run_prepare(observed=observed)
        model.assert_not_called()
        self.assertEqual(repeated['body'],first['body'])
        self.assertEqual(repeated['titles'],first['titles'])
        self.assertEqual(repeated['fields']['photoEvidence'][0]['confidence'],.99)
        self.assertFalse(repeated['photoInventory']['textUpdated'])
        self.assertEqual(self.catalogue()[self.old_id]['contentEditedAt'],before['contentEditedAt'])

    def test_review_only_findings_do_not_call_the_text_model(self):
        observed=fixtures.inventory();observed.update(features=[],photoEvidence=[])
        saved,model=self.run_prepare(observed=observed)
        model.assert_not_called()
        self.assertEqual(saved['body'],self.original['body'])
        self.assertTrue(saved['fields']['photoEdits']['observations'][fixtures.PHOTO]['analysis']['candidates'])
        self.assertFalse(self.catalogue()[self.old_id]['contentEdited'])

    def test_manual_edit_during_generation_wins_and_retains_photo_inventory(self):
        expected={}
        def manual(directory,data,**kwargs):
            concurrent=local_server.read_result(directory,self.old_id)
            concurrent.update(body='人工编辑正文',titles=['人工编辑标题'],tags='#人工标签')
            concurrent['fields']['price']='81000'
            local_server.save_result(directory,concurrent,self.old_id)
            expected.update(card=concurrent,state=self.catalogue()[self.old_id])
            return copy.deepcopy(MODEL_DRAFT)
        saved,model=self.run_prepare(draft=manual)
        model.assert_called_once()
        for key in ('fields','body','titles','tags'):
            self.assertEqual(saved[key],expected['card'][key])
        self.assertTrue(saved['photoInventory']['photoEvidence'])
        self.assertFalse(saved['photoInventory']['textUpdated'])
        self.assertEqual(self.catalogue()[self.old_id]['contentEditedAt'],expected['state']['contentEditedAt'])

    def test_catalogue_failure_rolls_back_payload_and_media_together(self):
        before=local_server.read_result(self.directory,self.old_id)
        prepared=copy.deepcopy(before);prepared['mediaStatus']='fixture-ready'
        generated={**copy.deepcopy(before),'body':'不能留下只更新了一半的正文。'}
        with patch.object(card_catalog,'record_save',side_effect=ValueError('isolated catalogue failure')):
            with self.assertRaisesRegex(ValueError,'isolated catalogue failure'):
                local_server.merge_prepared_media(self.directory,self.old_id,prepared,
                    photo_baseline=before,photo_generated=generated)
        self.assertEqual(local_server.read_result(self.directory,self.old_id),before)
        self.assertFalse(self.catalogue()[self.old_id]['contentEdited'])


if __name__=='__main__':unittest.main()
