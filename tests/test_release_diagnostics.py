import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import release_diagnostics as diagnostic


class ReleaseDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.root=self.base/'source';self.cache=self.base/'cache'
        for folder in ('web','cli','scripts','data','docs'):(self.root/folder).mkdir(parents=True)
        self.cache.mkdir()
        (self.root/'web/app.js').write_text('export const version=1;')
        (self.root/'cli/server.py').write_text('VERSION=1\n')
        (self.root/'web/rental-lexicon.mjs').write_text('export const words={};')
        self.git=patch.object(diagnostic,'_git',return_value={'commit':'a'*40,'dirty':False});self.git.start()
        self.history=patch('model_history.public_history',return_value={'total':0,'candidates':[]});self.history.start()
        identity=diagnostic.write_build_identity(self.root);self.build=identity['buildId']
        for name in ('index.html','phone.html'):
            (self.root/'docs'/name).write_text('<!doctype html><head><meta name="redcn-build" content="'+self.build+'"></head>')

    def tearDown(self):
        self.history.stop();self.git.stop();self.temp.cleanup()

    def snapshot(self):return {str(path.relative_to(self.base)):hashlib.sha256(path.read_bytes()).hexdigest() for path in self.base.rglob('*') if path.is_file()}

    def test_old_tab_partial_update_and_restart_identity_are_distinct(self):
        before=self.snapshot()
        value=diagnostic.versions(self.cache,self.root,server_release='v29',client_build=self.build)
        self.assertTrue(value['compatible']);self.assertEqual(value['serverRelease'],'v29')
        old=diagnostic.versions(self.cache,self.root,client_build='b'*64)
        self.assertIn('client-tab-outdated',old['mismatches'])
        self.assertEqual(before,self.snapshot())
        (self.root/'cli/server.py').write_text('VERSION=2\n')
        changed=diagnostic.versions(self.cache,self.root,client_build=self.build)
        self.assertIn('server-source-changed',changed['mismatches'])
        fresh=diagnostic.write_build_identity(self.root)
        self.assertNotEqual(fresh['buildId'],self.build)
        restarted=diagnostic.versions(self.cache,self.root,client_build=self.build)
        self.assertIn('desktop-shell-outdated',restarted['mismatches'])
        self.assertIn('phone-shell-outdated',restarted['mismatches'])
        self.assertNotIn('server-source-changed',restarted['mismatches'])

    def test_rebuilt_disk_does_not_certify_the_old_running_server(self):
        running=diagnostic.runtime_identity(self.root)
        initial=diagnostic.versions(self.cache,self.root,client_build=self.build,server_runtime=running)
        self.assertTrue(initial['compatible']);self.assertEqual(initial['serverBuildId'],self.build)
        (self.root/'cli/server.py').write_text('VERSION=2\n')
        rebuilt=diagnostic.write_build_identity(self.root)
        for name in ('index.html','phone.html'):
            (self.root/'docs'/name).write_text('<meta name="redcn-build" content="'+rebuilt['buildId']+'">')
        old=diagnostic.versions(self.cache,self.root,client_build=rebuilt['buildId'],server_runtime=running)
        self.assertEqual(old['mismatches'],['server-process-outdated'])
        self.assertEqual(old['serverBuildId'],self.build)
        self.assertEqual(old['sourceOnDiskBuildId'],rebuilt['buildId'])
        restarted=diagnostic.versions(self.cache,self.root,client_build=rebuilt['buildId'],server_runtime=diagnostic.runtime_identity(self.root))
        self.assertTrue(restarted['compatible']);self.assertEqual(restarted['serverBuildId'],rebuilt['buildId'])

    def test_preview_and_zip_are_allowlisted_read_only_bounded_and_self_describing(self):
        secret='PRIVATE-SENTINEL-owner@example.test-cookie-Bearer-12345'
        (self.cache/'phone-profile.json').write_text(json.dumps({'rules':{'private-term':secret},'token':secret}))
        (self.cache/'text-ai.json').write_text(json.dumps({'modelPath':str(self.base/'private-home'),'adapterPath':secret,'token':secret}))
        (self.cache/'photo-ai.json').write_text(json.dumps({'model':secret,'endpoint':secret}))
        (self.cache/'learning').mkdir();(self.cache/'learning/scheduler-state.json').write_text(json.dumps({'phase':secret,'error':secret,'elapsedSeconds':2}))
        folder=self.cache/'import-diagnostics';folder.mkdir()
        with sqlite3.connect(folder/'campaigns.sqlite3') as db:
            db.execute('CREATE TABLE attempts (outcome TEXT,elapsed REAL,error_class TEXT,started REAL,url TEXT,error_message TEXT)')
            db.execute('CREATE TABLE events (attempt_id TEXT,sequence INTEGER,payload TEXT)')
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',('error',4.5,secret,1,secret,secret))
            db.execute('INSERT INTO events VALUES (?,?,?)',(secret,0,json.dumps({'stage':'vision','elapsedSeconds':1,'message':secret})))
            db.execute('INSERT INTO events VALUES (?,?,?)',(secret,1,json.dumps({'stage':'save','elapsedSeconds':3,'message':secret})))
        self.history.return_value={'total':1,'candidates':[{'id':'a'*20,'state':'trained-unaccepted','selected':True,
            'weightsSha256':'b'*64,'stepsCompleted':20,'path':secret,'prompt':secret,
            'historicalEvaluation':{'reportSha256':'c'*64,'decision':{'accepted':False,'reason':secret}}}]}
        before=self.snapshot()
        preview,files=diagnostic.package_preview(self.cache,self.root,client_build=self.build)
        payload=diagnostic.package_bytes(self.cache,self.root,expected_preview=preview['previewSha256'],client_build=self.build)
        self.assertEqual(before,self.snapshot());self.assertLess(len(payload),diagnostic.MAX_PACKAGE_BYTES)
        self.assertFalse(preview['temporaryFilesCreated']);self.assertFalse(preview['externalSend'])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(set(archive.namelist()),{'manifest.json','versions.json','operations.json','training.json'})
            for name in archive.namelist():
                raw=archive.read(name).decode();self.assertNotIn(secret,raw);self.assertNotIn(str(self.base),raw)
            operations=json.loads(archive.read('operations.json'))
            self.assertEqual(operations['imports']['errors'],{'other':1})
            self.assertEqual(operations['imports']['stageIntervals']['vision']['p50Seconds'],2)
            self.assertEqual(operations['training']['phase'],'unknown')

    def test_changed_preview_oversize_and_traversal_are_rejected(self):
        preview,_=diagnostic.package_preview(self.cache,self.root)
        (self.root/'web/app.js').write_text('export const version=2;')
        with self.assertRaisesRegex(ValueError,'изменились'):diagnostic.package_bytes(self.cache,self.root,expected_preview=preview['previewSha256'])
        with patch.object(diagnostic,'operation_aggregates',return_value={'huge':'x'*(diagnostic.MAX_PACKAGE_BYTES+1)}):
            with self.assertRaisesRegex(ValueError,'превышает'):diagnostic.package_preview(self.cache,self.root)
        outside=self.base/'outside';outside.write_text('private')
        self.assertIsNone(diagnostic._file_sha(self.cache/'../outside',self.cache))
        (self.cache/'linked').symlink_to(outside)
        self.assertIsNone(diagnostic._file_sha(self.cache/'linked',self.cache))
        with self.assertRaises(ValueError):diagnostic.package_preview(self.cache,self.root,selected_run='../outside')

    def test_dispatch_requires_explicit_download_with_preview_and_returns_only_safe_data(self):
        status,body,mime,headers=diagnostic.dispatch(self.cache,self.root,'release-diagnostics',{'action':'status'},server_release='v29')
        self.assertEqual(status,200);self.assertTrue(body['readOnly']);self.assertEqual(headers,{})
        with self.assertRaises(ValueError):diagnostic.dispatch(self.cache,self.root,'release-diagnostics',{'action':'download'})
        with self.assertRaises(ValueError):diagnostic.dispatch(self.cache,self.root,'release-diagnostics',{'action':'preview','path':'private'})
        self.assertIsNone(diagnostic.dispatch(self.cache,self.root,'other'))


if __name__=='__main__':unittest.main()
