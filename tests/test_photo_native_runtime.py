import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import photo_native_runtime as native
import trained_photo_detector as detector


class NativePhotoRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.cache=Path(self.temp.name)
        self.folder=self.cache/native.FOLDER
        self.base=self.cache/'python-base';self.base.write_bytes(b'pinned signed Python fixture')
        python=self.folder/'venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(self.base)
        self.ort=self.folder/native.ORT_PATH;self.ort.parent.mkdir(parents=True);self.ort.write_bytes(b'pinned signed ORT fixture')
        self.model=self.cache/native.MODEL_PATH;self.model.parent.mkdir(parents=True);self.model.write_bytes(b'pinned model fixture')
        (self.folder/'venv/pyvenv.cfg').write_text('home = '+str(self.base.parent)+'\ninclude-system-site-packages = false\nversion = 3.11.9\n')
        site=self.folder/'venv/lib/python3.11/site-packages'
        for name,(version,_) in native.WHEELS.items():
            metadata=site/(name+'-'+version+'.dist-info');metadata.mkdir();(metadata/'METADATA').write_text('Name: '+name+'\nVersion: '+version+'\n')
        self.patches=[patch.object(native,'PYTHON_BASE',self.base),patch.object(native,'PYTHON_SHA',hashlib.sha256(self.base.read_bytes()).hexdigest()),
            patch.object(native,'ORT_SHA',hashlib.sha256(self.ort.read_bytes()).hexdigest()),patch.object(native,'MODEL_SHA',hashlib.sha256(self.model.read_bytes()).hexdigest()),
            patch.object(native.sys,'platform','darwin'),patch.object(native,'_codesign')]
        for item in self.patches:item.start()
        self.receipt=self.folder/'native-runtime.json';self.receipt.write_text(json.dumps(native.expected_receipt()))

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()

    def test_pinned_native_installation_selects_fixed_isolated_arm_command(self):
        command,identity=native.runtime_command(self.cache)
        self.assertEqual(command[:2],['/usr/bin/arch','-arm64']);self.assertEqual(command[-1],'-I')
        self.assertEqual(Path(command[2]),self.folder/'venv/bin/python')
        self.assertEqual(identity['status'],'verified');self.assertTrue(identity['ortSignatureVerified'])
        self.assertEqual(identity['receiptSha256'],hashlib.sha256(self.receipt.read_bytes()).hexdigest())

    def test_missing_optional_receipt_preserves_legacy_without_installing_anything(self):
        self.receipt.unlink();before=set(self.cache.rglob('*'))
        command,identity=native.runtime_command(self.cache)
        self.assertEqual(command,[sys.executable]);self.assertEqual(identity['selection'],'legacy')
        self.assertEqual(set(self.cache.rglob('*')),before)

    def test_receipt_cannot_select_an_arbitrary_executable_or_download_url(self):
        document=json.loads(self.receipt.read_text());document.update(pythonPath='/tmp/attacker',downloadUrl='https://evil.test/runtime')
        self.receipt.write_text(json.dumps(document))
        with self.assertRaisesRegex(native.NativeRuntimeError,'Паспорт'):native.runtime_command(self.cache)

    def test_receipt_types_cannot_substitute_boolean_for_version(self):
        document=json.loads(self.receipt.read_text());document['version']=True
        self.receipt.write_text(json.dumps(document))
        with self.assertRaisesRegex(native.NativeRuntimeError,'Паспорт'):native.runtime_command(self.cache)

    def test_tampered_binary_or_model_fails_instead_of_silent_legacy_fallback(self):
        for target in (self.base,self.ort,self.model):
            old=target.read_bytes();target.write_bytes(b'altered fixture')
            with self.subTest(target=target),self.assertRaisesRegex(native.NativeRuntimeError,'Контрольная сумма'):native.runtime_command(self.cache)
            target.write_bytes(old)

    def test_python_target_and_package_versions_are_guarded(self):
        python=self.folder/'venv/bin/python';python.unlink();python.symlink_to(self.ort)
        with self.assertRaisesRegex(native.NativeRuntimeError,'Путь Python'):native.runtime_command(self.cache)
        python.unlink();python.symlink_to(self.base)
        metadata=next((self.folder/'venv/lib/python3.11/site-packages').glob('numpy-*.dist-info/METADATA'))
        metadata.write_text('Name: numpy\nVersion: 0.0.0\n')
        with self.assertRaisesRegex(native.NativeRuntimeError,'Версии пакетов'):native.runtime_command(self.cache)

    def test_unsigned_runtime_is_rejected_before_worker_launch(self):
        with patch.object(native,'_codesign',side_effect=native.NativeRuntimeError('Подпись ARM runtime не подтверждена')),patch.object(detector.subprocess,'Popen') as launch:
            result=detector.analyze(b'photo','image/jpeg',self.cache)
        self.assertEqual(result['errorCode'],'PHOTO_NATIVE_RUNTIME_INTEGRITY');self.assertEqual(result['runtime']['status'],'invalid')
        self.assertEqual(result['candidates'],[]);self.assertFalse(result['autoApply']);launch.assert_not_called()

    def test_worker_architecture_is_checked_and_publication_policy_stays_review_only(self):
        class Process:
            returncode=0
            architecture='arm64'
            def communicate(self,**kwargs):
                return json.dumps({'provider':'yolox-trained','model':detector.MODEL,'runtimeArchitecture':self.architecture,
                    'autoApply':True,'candidates':[{'id':'sofa','autoEligible':True,'autoCountEligible':True,'count':8}]}).encode(),b''
            def poll(self):return 0
        process=Process()
        with patch.object(detector.subprocess,'Popen',return_value=process) as start:
            result=detector.analyze(b'photo','image/jpeg',self.cache)
            self.assertIn('-I',start.call_args.args[0]);self.assertEqual(result['runtime']['workerArchitecture'],'arm64')
            self.assertFalse(result['autoApply']);self.assertFalse(result['candidates'][0]['autoEligible']);self.assertIsNone(result['candidates'][0]['count'])
            process.architecture='x86_64';wrong=detector.analyze(b'photo','image/jpeg',self.cache)
        self.assertEqual(wrong['errorCode'],'PHOTO_NATIVE_RUNTIME_ARCHITECTURE');self.assertEqual(wrong['candidates'],[])

    def test_runtime_receipt_changes_observation_cache_identity(self):
        first=native.cache_identity(self.cache)
        document=json.loads(self.receipt.read_text());document['architecture']='wrong';self.receipt.write_text(json.dumps(document))
        self.assertNotEqual(first,native.cache_identity(self.cache))


if __name__=='__main__':unittest.main()
