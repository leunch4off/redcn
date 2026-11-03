"""Private weights and withdrawals survive transfer without carrying approval."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import test_workspace_backup as fixtures
import learning_transfer as transfer
import workspace_backup as backup
from learning_memory import LearningMemory
from learning_scheduler import save_config,load_config


class LegacySchedulerTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=Path(__file__).parent/'fixtures/macos-learning-v29'
        with zipfile.ZipFile(cls.fixture/'workspace.zip') as archive:
            cls.package=json.loads(archive.read('metadata.json'))['learningPackage']
            cls.files={name:archive.read(name) for name in archive.namelist()
                if name.startswith('learning/artifacts/')}

    def test_existing_v29_mac_fixture_still_passes_complete_portability_proof(self):
        from portability_proof import verify_fixture
        content=(self.fixture/'workspace.zip').read_bytes()
        receipt=json.loads((self.fixture/'source.json').read_text())
        before=copy.deepcopy(receipt)
        self.assertNotIn('advertisingMaxSequenceLength',self.package['scheduler'])
        result=verify_fixture(content,receipt)
        self.assertTrue(result['verified'])
        self.assertTrue(result['learningTransferVerified'])
        self.assertTrue(result['freshNativeModelAcceptanceRequired'])
        self.assertFalse(result['realModelQualityEvaluated'])
        self.assertEqual(receipt,before,'Never rewrite the historical source receipt')

    def test_old_scheduler_gets_new_safe_default_without_enabling_or_changing_history(self):
        package=copy.deepcopy(self.package)
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)
            # Missing old-package fields must not inherit an unrelated local
            # override instead of the new version's reviewed safe default.
            save_config(destination,{'advertisingMaxSequenceLength':512})
            result=transfer.restore(destination,package,self.files)
            config=load_config(destination)
            self.assertEqual(config['advertisingMaxSequenceLength'],1536)
            self.assertEqual(config['maxSteps'],37)
            self.assertFalse(config['enabled'])
            self.assertFalse(config['automaticPromotion'])
            self.assertFalse((destination/'text-ai.json').exists())
            self.assertTrue(result['freshAcceptanceRequired'])
            self.assertFalse(transfer.preflight(destination)['readyToActivate'])
            self.assertEqual(json.loads((destination/'learning/restored-ai.json').read_text()),self.package)
        self.assertEqual(package,self.package)

    def test_supported_new_limit_is_preserved_and_invalid_or_unknown_fields_veto_restore(self):
        package=copy.deepcopy(self.package)
        package['scheduler']['advertisingMaxSequenceLength']=1024
        with tempfile.TemporaryDirectory() as temp:
            transfer.restore(temp,package,self.files)
            self.assertEqual(load_config(temp)['advertisingMaxSequenceLength'],1024)
        invalid=[{'advertisingMaxSequenceLength':value} for value in (511,1537,True,'1536',None)]
        invalid += [{'unknownOption':True},{'enabled':1},{'maxSteps':True},{'checkSeconds':0}]
        for update in invalid:
            forged=copy.deepcopy(self.package);forged['scheduler'].update(update)
            with self.subTest(update=update),tempfile.TemporaryDirectory() as temp:
                destination=Path(temp)/'not-created'
                with self.assertRaises(ValueError):transfer.restore(destination,forged,self.files)
                self.assertFalse(destination.exists())
        incomplete=copy.deepcopy(self.package);del incomplete['scheduler']['maxSteps']
        with self.assertRaises(ValueError):transfer.validate(incomplete,self.files)


class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.WorkspaceBackupTests.setUpClass()

    def setUp(self):
        self.fixture=fixtures.WorkspaceBackupTests('test_portable_library_dictionary_learning_history_and_all_versions_restore_together')
        self.fixture.setUp()
        self.source=self.fixture.source
        self.destination=self.fixture.destination

    def tearDown(self):self.fixture.tearDown()

    def model(self):
        folder=self.source/'tools/text-ai/runs/fixture'
        folder.mkdir(parents=True)
        content=b'non-executable-synthetic-weights'
        digest=hashlib.sha256(content).hexdigest()
        (folder/'adapters.safetensors').write_bytes(content)
        (folder/'trainreceipt.json').write_text(json.dumps({'status':'trained-unaccepted',
            'adapterSha256':digest,'stepsCompleted':2,'privatePath':'/Users/private-owner'}))
        (self.source/'text-ai.json').write_text(json.dumps({'adapterPath':str(folder),
            'useCandidateAdapter':True,'enabled':True,'advertisingEnabled':True}))
        return digest,content

    def test_source_phrase_queue_and_review_state_survive_transfer(self):
        from learning_discovery import capture_import,status,review_phrase
        capture_import(self.source,{'fields':{'sourceDescription':'В комнате установлен письменный стол. Окна выходят во двор.'}},self.fixture.one)
        first=status(self.source)['candidates'][0]
        review_phrase(self.source,first['id'],'人工确认的译文。')
        before=status(self.source)
        backup.restore_workspace(backup.export_workspace(self.source),self.destination)
        after=status(self.destination)
        for key in ('uniqueSourcePhrases','pendingTranslation','indexedCards','lastCapture','candidates'):
            self.assertEqual(after[key],before[key])
        self.assertFalse(after['modelWeightsUpdated'])
        self.assertEqual(LearningMemory(self.destination).status()['approvedExamples'],2)

    def test_preserves_withdrawal_and_weights_but_restarts_with_training_off(self):
        digest,content=self.model()
        save_config(self.source,{'enabled':True,'automaticPromotion':True,'maxSteps':87})
        memory=LearningMemory(self.source)
        event=memory.record('translation',{'ru':'Только вымышленный чайник','zh':'仅虚构水壶'},status='approved',actor='Mac owner')
        memory.withdraw(event['id'],actor='Mac owner',reason='withdraw fixture')
        data=backup.export_workspace(self.source)
        self.assertEqual(backup.inspect_workspace(data)['modelVersions'],1)
        receipt=backup.restore_workspace(data,self.destination)
        self.assertTrue(receipt['modelsRestored'])
        self.assertTrue(receipt['learningRestore']['freshAcceptanceRequired'])
        restored=load_config(self.destination)
        self.assertEqual(restored['maxSteps'],87)
        self.assertFalse(restored['enabled']);self.assertFalse(restored['automaticPromotion'])
        self.assertFalse((self.destination/'text-ai.json').exists())
        folder=self.destination/'learning/imported-models'/digest
        self.assertEqual((folder/'adapters.safetensors').read_bytes(),content)
        self.assertNotIn('/Users/private-owner',(folder/'historical-trainreceipt.json').read_text())
        with self.assertRaisesRegex(ValueError,'отозван'):
            LearningMemory(self.destination).record('translation',{'ru':'Только вымышленный чайник','zh':'仅虚构水壶'},status='approved')
        self.assertFalse(transfer.preflight(self.destination)['readyToActivate'])

    def test_altered_weight_or_forged_path_rejects_before_destination_exists(self):
        self.model()
        package,files=transfer.snapshot(self.source)
        changed=dict(files);changed[next(iter(changed))]=b'corrupt'
        with self.assertRaisesRegex(ValueError,'контрольной'):transfer.validate(package,changed)
        forged=copy.deepcopy(package);forged['models'][0]['blob']='../../private'
        with self.assertRaises(ValueError):transfer.validate(forged,files)

    def test_nested_private_fields_are_removed_while_model_evidence_survives_restore(self):
        from text_model_contract import BASE_MODEL,LORA
        from trained_photo_detector import ARTIFACT,CLASS_IDS
        digest,_=self.model()
        path=self.source/'tools/text-ai/runs/fixture/trainreceipt.json'
        private='/Users/private-owner/raw-training-source'
        text_receipt={'version':1,'status':'trained-unaccepted','baseModel':BASE_MODEL,
            'adapterSha256':digest,'datasetSha256':'d'*64,'datasetContentSha256':'e'*64,
            'baseConfigSha256':'c'*64,'baseArtifacts':{'model-00001-of-00002.safetensors':'a'*64,private:'f'*64},
            'stepsCompleted':87,'trainableParameters':540672,'peakMemoryBytes':3_000_000_000,
            'elapsedSeconds':42.5,'baselineValidationLoss':2.5,'finalValidationLoss':1.75,
            'sampling':'random','learningRate':.00005,'stoppedEarly':False,
            'examples':{'train':134,'validation':80,'rawPrompt':'PRIVATE RAW EXAMPLE','privatePath':private},
            'limits':{'seconds':600,'steps':87,'maxSequenceLength':768,'memoryBytes':3_500_000_000,'batchSize':1,'command':private},
            'lora':{**LORA,'rawPrompt':'PRIVATE RAW EXAMPLE'},
            'packages':{'mlx':'0.29.2','mlx-lm':'0.28.4','torch':'2.5.1+cpu','Pillow':'11.3.0',
                'onnxruntime':private,'rawPrompt':'PRIVATE RAW EXAMPLE'}}
        path.write_text(json.dumps(text_receipt))
        photo=self.source/ARTIFACT;photo.mkdir(parents=True)
        content=b'non-executable-synthetic-onnx';photo_sha=hashlib.sha256(content).hexdigest()
        (photo/'model.onnx').write_bytes(content)
        (photo/'trainreceipt.json').write_text(json.dumps({'version':1,'status':'trained-unaccepted',
            'manifestSha256':'f'*64,'pretrainedSha256':'b'*64,'completedSteps':60,
            'trainImages':134,'validationImages':80,'seconds':23.5,
            'onnx':{'sha256':photo_sha,'path':'model.onnx','bytes':len(content),
                'inputShape':[1,3,416,416],'outputShape':[1,3549,26],
                'outputEncoding':'raw-yolox','classIds':CLASS_IDS,'rawPrompt':'PRIVATE RAW EXAMPLE',
                'source':{'path':private}}}))
        package,files=transfer.snapshot(self.source)
        transfer.validate(package,files)
        self.assertEqual(len(package['models']),2)
        for item in package['models']:
            encoded=json.dumps(item['receipt'])
            self.assertNotIn(private,encoded);self.assertNotIn('PRIVATE RAW EXAMPLE',encoded)
        data=backup.export_workspace(self.source)
        result=backup.restore_workspace(data,self.destination)
        self.assertEqual(result['learningRestore']['versions'],2)
        def restored(sha):
            return json.loads((self.destination/'learning/imported-models'/sha/'historical-trainreceipt.json').read_text())
        text=restored(digest)
        self.assertEqual(text['examples'],{'train':134,'validation':80})
        self.assertEqual(text['baseArtifacts'],{'model-00001-of-00002.safetensors':'a'*64})
        self.assertEqual(text['lora'],LORA)
        self.assertEqual(text['packages'],{'mlx':'0.29.2','mlx-lm':'0.28.4','torch':'2.5.1+cpu','Pillow':'11.3.0'})
        for key in ('adapterSha256','datasetSha256','datasetContentSha256','baseConfigSha256',
                    'stepsCompleted','trainableParameters','peakMemoryBytes','elapsedSeconds',
                    'baselineValidationLoss','finalValidationLoss','sampling','learningRate','stoppedEarly'):
            self.assertEqual(text[key],text_receipt[key])
        photo_receipt=restored(photo_sha)
        self.assertEqual(photo_receipt['completedSteps'],60)
        self.assertEqual(photo_receipt['manifestSha256'],'f'*64)
        self.assertEqual(photo_receipt['onnx'],{'sha256':photo_sha,'path':'model.onnx','bytes':len(content),
            'inputShape':[1,3,416,416],'outputShape':[1,3549,26],'outputEncoding':'raw-yolox','classIds':CLASS_IDS})
        self.assertFalse(transfer.preflight(self.destination)['readyToActivate'])

    def test_import_rejects_injected_nested_paths_and_raw_examples(self):
        self.model();package,files=transfer.snapshot(self.source)
        additions=[
            {'examples':{'train':1,'rawPrompt':'PRIVATE EXAMPLE'}},
            {'examples':{'train':{'source':'/Users/private'}}},
            {'baseArtifacts':{'/Users/private/model.safetensors':'a'*64}},
            {'baseArtifacts':{'../model.safetensors':'a'*64}},
            {'baseArtifacts':{'model.safetensors':'/Users/private'}},
            {'packages':{'/Users/private':'1.0.0'}},
            {'packages':{'mlx':'file:///Users/private'}},
            {'packages':{'mlx':'1.0.0 PRIVATE EXAMPLE'}},
            {'packages':{'mlx':'1.'+'0'*80}},
            {'lora':{'rank':8,'keys':['self_attn.q_proj'],'rawPrompt':'PRIVATE EXAMPLE'}},
            {'lora':{'keys':['self_attn.q_proj','/Users/private']}},
            {'limits':{'seconds':600,'command':['/Users/private']}},
            {'onnx':{'sha256':'a'*64,'path':'/Users/private/model.onnx'}},
            {'onnx':{'inputShape':[1,3,'PRIVATE EXAMPLE',416]}},
            {'onnx':{'classIds':['sofa','PRIVATE EXAMPLE']}},
            {'onnx':{'outputEncoding':'https://private.example/weights'}},
            {'baseModel':'/Users/private/model'},
            {'status':'PRIVATE EXAMPLE'}]
        for addition in additions:
            with self.subTest(field=next(iter(addition))):
                forged=copy.deepcopy(package);forged['models'][0]['receipt'].update(addition)
                with self.assertRaisesRegex(ValueError,'непереносимые'):transfer.validate(forged,files)

    def test_import_rejects_nonfinite_unbounded_and_boolean_measurements(self):
        self.model();package,files=transfer.snapshot(self.source)
        additions=[{'stepsCompleted':True},{'peakMemoryBytes':2**53},
            {'elapsedSeconds':float('nan')},{'finalValidationLoss':float('inf')},
            {'elapsedSeconds':10**500},{'examples':{'train':-1}},
            {'onnx':{'inputShape':[1,3,0,416]}},{'onnx':{'inputShape':[True,3,416,416]}},
            {'onnx':{'inputShape':[1]*9}},{'onnx':{'outputShape':[1,1_000_000,1_000_000]}},
            {'onnx':{'classIds':['sofa']*101}},{'onnx':{'classIds':['sofa','sofa']}},
            {'onnx':{'bytes':10_000_001}},{'lora':{'dropout':2}},
            {'baseArtifacts':{f'model-{part:05d}-of-00051.safetensors':'a'*64 for part in range(1,52)}}]
        for addition in additions:
            with self.subTest(field=next(iter(addition))):
                forged=copy.deepcopy(package);forged['models'][0]['receipt'].update(addition)
                with self.assertRaisesRegex(ValueError,'непереносимые'):transfer.validate(forged,files)

    def test_malformed_photo_receipt_cannot_break_export_of_valid_text_weights(self):
        from trained_photo_detector import ARTIFACT
        self.model();folder=self.source/ARTIFACT;folder.mkdir(parents=True)
        (folder/'model.onnx').write_bytes(b'synthetic-incomplete-onnx')
        (folder/'trainreceipt.json').write_text(json.dumps({'onnx':'/Users/private/unfinished'}))
        package,files=transfer.snapshot(self.source)
        transfer.validate(package,files)
        self.assertEqual([row['kind'] for row in package['models']],['text'])

    def test_legacy_bundle_without_learning_package_remains_restorable(self):
        manifest,files=backup._read(backup.export_workspace(self.source))
        metadata=json.loads(files['metadata.json']);metadata.pop('learningPackage')
        files['metadata.json']=backup._json(metadata)
        data=backup._pack({key:value for key,value in manifest.items() if key!='files'},files)
        result=backup.restore_workspace(data,self.destination)
        self.assertIsNone(result['learningRestore'])
        self.assertFalse(result['modelsRestored'])


if __name__=='__main__':unittest.main()
