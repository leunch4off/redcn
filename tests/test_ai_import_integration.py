"""Optional ad generation follows photo facts and preserves concurrent edits."""
import copy
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_content
import local_server
import local_text_model
import node_runtime
import photo_generation
import photo_store
import phone_relay
import redcn

URL='https://www.cian.ru/rent/flat/300691182/'
BASE={'fields':{'photos':[],'price':'80000','features':[]},'titles':['标题'],'body':'正文','tags':'#租房'}


class AiImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.server=local_server.create_server(self.directory,0)

    def tearDown(self):
        self.server.server_close();self.temp.cleanup()

    def run_import(self, *, enabled=True, helper=None, settings_error=None):
        events=[]
        def photos(directory,result,report=None,cancel=None):
            events.append('photos');result.update(media=[],mediaStatus='ready')
        def revised(directory,result,**kwargs):
            events.append('photo-facts')
            return {**copy.deepcopy(BASE),'fields':{**BASE['fields'],'features':['fridge']},'body':'冰箱'}
        def advertise(directory,result,**kwargs):
            events.append('advertisement')
            self.assertEqual(events,['photos','photo-facts','advertisement'])
            self.assertEqual(result['fields']['features'],['fridge'])
            kwargs['status']('ИИ завершил черновик',stage='generate',ai={
                'backend':'mlx','operation':'text','phase':'complete','generatedTokens':30,'tokensPerSecond':10,'generationSeconds':3})
            if helper:return helper(directory,result)
            return {**result,'body':'广告正文','aiAdvertisement':{'status':'validated','fallback':False}}
        with patch.object(node_runtime,'resolve_node',return_value='/fixture/node'), \
                patch.object(photo_store,'pillow',return_value=None), \
                patch.object(redcn,'load_listing',return_value={'url':URL,'text':'Synthetic apartment facts','images':[],'cached':True}), \
                patch.object(redcn,'generate',side_effect=lambda *args,**kwargs:copy.deepcopy(BASE)), \
                patch.object(phone_relay,'cache_photos',side_effect=photos), \
                patch.object(photo_generation,'photo_revision',side_effect=revised), \
                patch.object(local_text_model,'settings',side_effect=settings_error,return_value={'advertisingEnabled':enabled}), \
                patch.object(local_content,'generate_import_advertisement',side_effect=advertise,create=True) as model:
            existing=set(threading.enumerate())
            job_id=self.server.start_import({'url':URL,'loader':'http'})
            workers=[worker for worker in threading.enumerate() if worker not in existing]
            try:
                until=time.monotonic()+3
                while time.monotonic()<until:
                    job=self.server.job_status(job_id)
                    if job['state']!='running':break
                    time.sleep(.01)
                else:self.fail('Synthetic import timed out')
            finally:
                # The API marks completion before its final audit writes. Join
                # this fixture's worker before deleting its isolated database.
                for worker in workers:worker.join(3)
            self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(job['state'],'done',job)
        return local_server.read_result(self.directory,job['result_id']),job,model.call_count

    def test_advertisement_runs_once_after_photo_facts_and_keeps_metrics(self):
        saved,job,calls=self.run_import()
        self.assertEqual(calls,1);self.assertEqual(saved['body'],'广告正文')
        self.assertEqual(saved['aiAdvertisement']['status'],'validated')
        self.assertEqual(saved['fields']['features'],['fridge'])
        self.assertEqual(job['progress']['ai']['generatedTokens'],30)
        self.assertFalse(job['progress']['ai']['active'])

    def test_disabled_advertising_does_not_run_model(self):
        saved,job,calls=self.run_import(enabled=False)
        self.assertEqual(calls,0);self.assertEqual(saved['body'],'冰箱')

    def test_concurrent_manual_edit_survives_advertisement_merge(self):
        def edit(directory,result):
            connection=redcn.open_cache(directory)
            try:result_id=connection.execute('SELECT id FROM results').fetchone()[0]
            finally:connection.close()
            manual=local_server.read_result(directory,result_id)
            manual['body']='手工编辑';manual['fields']['price']='81000'
            local_server.save_result(directory,manual,result_id)
            return {**result,'body':'广告正文','aiAdvertisement':{'status':'validated'}}
        saved,job,calls=self.run_import(helper=edit)
        self.assertEqual(calls,1);self.assertEqual(saved['body'],'手工编辑')
        self.assertEqual(saved['fields']['price'],'81000')
        self.assertNotIn('aiAdvertisement',saved)

    def test_broken_optional_settings_or_generation_keeps_factual_baseline(self):
        saved,job,calls=self.run_import(settings_error=ValueError('synthetic corrupt settings'))
        self.assertEqual(calls,0);self.assertEqual(saved['body'],'冰箱')
        self.assertEqual(job['progress']['ai']['phase'],'unavailable')
        def failed(directory,result):raise ValueError('synthetic model failure')
        saved,job,calls=self.run_import(helper=failed)
        self.assertEqual(calls,1);self.assertEqual(saved['body'],'冰箱')
        self.assertEqual(job['progress']['ai']['phase'],'unavailable')


class PhotoJournalPersistenceTests(unittest.TestCase):
    def test_refresh_preserves_manual_checkbox_and_review_journal(self):
        import listing_photo_inventory
        import photo_ai_settings
        journal={'version':1,'entries':[{'originalId':'a'*64,'featureId':'fridge','state':'pending','automatic':True}]}
        inventory={'state':'partial','features':[],'photoEvidence':[],'photos':[],
            'reviewJournal':journal,'autoApplyMode':'suggestions-only'}
        result={**copy.deepcopy(BASE),'fields':{**BASE['fields'],'features':['fridge'],
            'photoAutoFeatures':['fridge'],'photoFeatureBaseline':{'fridge':{'checked':True}}}}
        with patch.object(photo_ai_settings,'load_config',return_value={'maxSeconds':120,'autoApply':True}), \
                patch.object(listing_photo_inventory,'analyze_listing_photos',return_value=inventory), \
                patch.object(redcn,'generate',side_effect=lambda text,fields,rules,prefs:{**BASE,'fields':fields}):
            revised=photo_generation.photo_revision(Path('/unused-fixture'),result)
        self.assertEqual(revised['fields']['features'],['fridge'])
        self.assertEqual(revised['fields']['photoEdits']['reviewJournal'],journal)
        revised['fields']['photoEdits']['reviewJournal']['entries'][0]['state']='rejected'
        self.assertEqual(journal['entries'][0]['state'],'pending')


if __name__=='__main__':unittest.main()
