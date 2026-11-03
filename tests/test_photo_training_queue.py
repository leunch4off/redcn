import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from PIL import Image
import photo_store
import photo_training
import photo_training_api


class PhotoTrainingQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cache=Path(self.temp.name);self.card='a'*32;self.other='b'*32
        self.urls=['https://images.cdn-cian.ru/images/synthetic-label-a.jpg','https://images.cdn-cian.ru/images/synthetic-label-b.jpg']
        self.photos=[]
        for index,url in enumerate(self.urls):
            stream=io.BytesIO();Image.new('RGB',(40,40),['red','blue'][index]).save(stream,format='PNG')
            self.photos.append(photo_store.attach_photo(self.cache,url,'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()))
        self.result={'fields':{'photos':self.urls,'photoEdits':{'reviewJournal':{'entries':[
            {'originalId':self.photos[0]['id'],'featureId':'sofa','state':'confirmed','evidence':[
                {'originalId':self.photos[0]['id'],'region':[.1,.1,.4,.4]}]},
            {'originalId':self.photos[1]['id'],'featureId':'washer','state':'rejected','evidence':[]}]}}},
            'listing':{'images':self.urls}}
        self.store_card(self.card,self.result)

    def store_card(self,card,result):
        with sqlite3.connect(self.cache/'listings.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
            db.execute('INSERT OR REPLACE INTO results VALUES (?,?,?)',(card,0,json.dumps(result)))

    def document(self):
        view=photo_training.view(self.cache,self.card);doc=view['document']
        doc.update(groupId='fictional-apartment-one',split='train',duplicatesReviewed=True,
                   rights={'allowed':True,'basis':'owner-authorized','photoIds':sorted(row['id'] for row in view['photos'])})
        return doc

    def verified(self):
        doc=self.document()
        doc['rows'][0].update(state='present',verified=True,exhaustive=True)
        return doc

    def save(self,doc,revision=0):
        return photo_training.save(self.cache,self.card,doc,expected_revision=revision,actor='fixture-operator')

    def test_get_proposals_are_pending_even_after_listing_confirmation_and_no_db_created(self):
        value=photo_training.view(self.cache,self.card)
        self.assertEqual(value['revision'],0)
        self.assertEqual({r['sourceDecision'] for r in value['document']['rows']},{'confirmed','rejected'})
        self.assertTrue(all(r['state']=='pending' and not r['verified'] for r in value['document']['rows']))
        self.assertFalse((self.cache/'photo-training').exists())

    def test_export_only_verified_labels_preserves_unknown_and_rejection(self):
        doc=self.verified();doc['rows'][1].update(state='rejected')
        self.save(doc)
        result=photo_training.export_manifest(self.cache,[self.card])
        self.assertEqual(len(result['splits']['train']),1)
        self.assertEqual(result['excluded'],{'rejected':1})
        image=result['splits']['train'][0]
        self.assertEqual(image['verifiedClassIndices'],[0]);self.assertEqual(image['negativeClassIndices'],[])
        self.assertEqual(image['reviewedLabels'][0]['reviewedBy'],'fixture-operator')
        self.assertFalse(result['automaticAcceptance']);self.assertFalse(result['independenceVerified'])
        self.assertNotIn('path',image)

    def test_negative_requires_explicit_exhaustive_verified_decision_and_zero_boxes(self):
        doc=self.document();row=doc['rows'][1];row.update(state='absent',verified=True,exhaustive=False)
        with self.assertRaises(ValueError):self.save(doc)
        row['exhaustive']=True;row['boxes']=[{'region':[.1,.1,.2,.2]}]
        with self.assertRaises(ValueError):self.save(doc)
        row['boxes']=[];self.save(doc)
        result=photo_training.export_manifest(self.cache,[self.card])
        self.assertEqual(result['splits']['train'][0]['negativeClassIndices'],[11])

    def test_unknown_cannot_be_verified(self):
        doc=self.document();doc['rows'][0].update(state='unknown',verified=True,exhaustive=True)
        with self.assertRaises(ValueError):self.save(doc)

    def test_foreign_photo_or_tampered_identity_and_actor_are_rejected(self):
        doc=self.verified();doc['rows'][0]['photoId']='f'*64
        with self.assertRaises(ValueError):self.save(doc)
        doc=self.verified();doc['rows'][0]['id']='f'*64
        with self.assertRaises(ValueError):self.save(doc)
        with self.assertRaises(ValueError):photo_training_api.dispatch(self.cache,'photo-training/'+self.card,
            {'action':'save','document':self.verified(),'expectedRevision':0,'actor':'forged'})

    def test_revision_conflict_does_not_overwrite_and_get_has_namespace(self):
        self.save(self.verified())
        response=photo_training_api.dispatch(self.cache,'photo-training/'+self.card,
            {'action':'save','document':self.verified(),'expectedRevision':0})
        self.assertEqual(response[0],409)
        value=photo_training.view(self.cache,self.card,namespace='phone')
        self.assertEqual(value['revision'],1)
        self.assertTrue(value['photos'][0]['url'].startswith('/api/phone/media/'))

    def test_replacing_original_revokes_rights_and_old_decisions_are_not_exported(self):
        self.save(self.verified());self.result['fields']['photos']=self.urls[1:];self.result['listing']['images']=self.urls[1:]
        self.store_card(self.card,self.result)
        value=photo_training.view(self.cache,self.card)
        self.assertFalse(value['document']['rights']['allowed'])
        old=next(row for row in value['document']['rows'] if row['photoId']==self.photos[0]['id'])
        self.assertTrue(old['stale'])
        result=photo_training.export_manifest(self.cache,[self.card])
        self.assertEqual(result['splits']['train'],[])

    def test_stale_editor_cannot_extend_permission_after_original_is_added(self):
        full=copy.deepcopy(self.result)
        self.result['fields']['photos']=self.urls[:1];self.result['listing']['images']=self.urls[:1]
        self.store_card(self.card,self.result);self.save(self.verified())
        stale=photo_training.view(self.cache,self.card)['document']
        self.store_card(self.card,full)
        response=photo_training_api.dispatch(self.cache,'photo-training/'+self.card,
            {'action':'save','document':stale,'expectedRevision':1})
        self.assertEqual(response[0],409)
        self.assertEqual(response[1]['code'],'PHOTO_TRAINING_REVISION_CONFLICT')
        current=photo_training.view(self.cache,self.card)
        self.assertEqual(current['revision'],1)
        self.assertFalse(current['document']['rights']['allowed'])
        self.assertEqual(photo_training.export_manifest(self.cache,[self.card])['splits']['train'],[])
        # A newly loaded editor can explicitly authorize this new exact set.
        fresh=current['document']
        fresh['rights'].update(allowed=True,photoIds=sorted(row['id'] for row in current['photos']))
        saved=self.save(fresh,revision=1)
        self.assertEqual(saved['revision'],2)
        self.assertTrue(saved['document']['rights']['allowed'])

    def test_save_without_displayed_original_set_is_a_conflict_and_writes_nothing(self):
        doc=self.verified();doc['rights'].pop('photoIds')
        response=photo_training_api.dispatch(self.cache,'photo-training/'+self.card,
            {'action':'save','document':doc,'expectedRevision':0})
        self.assertEqual(response[0],409)
        self.assertEqual(photo_training.view(self.cache,self.card)['revision'],0)
        self.assertFalse((self.cache/'photo-training').exists())

    def test_groups_cannot_cross_splits_and_same_original_cannot_be_two_apartments(self):
        self.save(self.verified());self.store_card(self.other,self.result)
        doc=self.verified();doc['listingId']=self.other;doc['split']='test'
        photo_training.save(self.cache,self.other,doc,expected_revision=0,actor='fixture-two')
        with self.assertRaisesRegex(ValueError,'разных частях'):photo_training.export_manifest(self.cache,[self.card,self.other])
        doc['split']='train';doc['groupId']='another-apartment'
        photo_training.save(self.cache,self.other,doc,expected_revision=1,actor='fixture-two')
        with self.assertRaisesRegex(ValueError,'разным квартирам'):photo_training.export_manifest(self.cache,[self.card,self.other])

    def test_view_group_and_permission_missing_keep_labels_outside_export(self):
        doc=self.verified();doc['rights']['allowed']=False;self.save(doc)
        result=photo_training.export_manifest(self.cache,[self.card])
        self.assertEqual(result['excluded'],{'image-use-permission-missing':1})

    def test_export_rechecks_original_bytes_and_private_db_rejects_links(self):
        self.save(self.verified());path=self.cache/'media'/self.photos[0]['id'];path.write_bytes(b'changed')
        with self.assertRaises(ValueError):photo_training.export_manifest(self.cache,[self.card])
        db=self.cache/'photo-training/reviews.sqlite3';renamed=db.with_suffix('.saved');db.rename(renamed);db.symlink_to(renamed)
        with self.assertRaises(PermissionError):photo_training.view(self.cache,self.card)

    def test_cli_export_is_private_refuses_overwrite_and_prints_only_aggregates(self):
        self.save(self.verified());dest=self.cache/'private-export'
        cmd=[sys.executable,str(ROOT/'scripts/photo-corpus.py'),'export','--cache',str(self.cache),'--card-id',self.card,'--output',str(dest)]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertEqual(json.loads(result.stdout)['imageCount'],1)
        self.assertNotIn(self.card,result.stdout);self.assertNotIn(self.urls[0],result.stdout)
        self.assertEqual(dest.stat().st_mode&0o777,0o700)
        manifest=json.loads((dest/'manifest.json').read_text());row=manifest['splits']['train'][0]
        self.assertEqual(hashlib.sha256((dest/row['path']).read_bytes()).hexdigest(),row['sha256'])
        self.assertNotEqual(subprocess.run(cmd,capture_output=True,text=True,timeout=15).returncode,0)


if __name__=='__main__':unittest.main()
