"""Encrypted transport and fresh restore use only generated public workspaces."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import cloud_backup as cloud
import portability_proof as fixture
import workspace_backup


def config():return {'version':1,'registered':True,'enabled':True,'origin':cloud.ORIGIN,'ownerId':'a'*64,'ownerKey':'b'*64,'cipherKey':'c'*64,'serviceBearer':'synthetic-service-access-only','recoveryAcknowledged':True}
class MemoryTransport:
 def __init__(self):self.files={};self.calls=[]
 def __call__(self,method,path,data=None,extra=None):
  self.calls.append((method,path));identifier=path.split('/')[-1]
  if method=='PUT':
   sha=hashlib.sha256(data).hexdigest();assert extra['X-Redcn-Cipher-Sha256']==sha;self.files[identifier]=data
   return 201,{},json.dumps({'id':identifier,'ownerId':'a'*64,'sha256':sha,'bytes':len(data),'stored':True}).encode()
  if method=='GET' and identifier=='backups':return 200,{},json.dumps({'ownerId':'a'*64,'items':[{'id':i,'sha256':hashlib.sha256(v).hexdigest(),'bytes':len(v)} for i,v in self.files.items()]}).encode()
  data=self.files[identifier];return 200,{'X-Redcn-Cipher-Sha256':hashlib.sha256(data).hexdigest()},data
class CloudBackupTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  try:cloud._aes()
  except ValueError as error:raise unittest.SkipTest(str(error))
  cls.archive,cls.receipt=fixture.generate_fixture()
 def test_encrypted_upload_download_fresh_restore_preserves_library_dictionary_media(self):
  transport=MemoryTransport();client=cloud.SiteR2Client(config(),transport=transport);receipt=client.upload(self.archive,identifier='d'*32)
  self.assertEqual(receipt['archiveSha256'],hashlib.sha256(self.archive).hexdigest());cipher=transport.files['d'*32]
  self.assertTrue(cipher.startswith(cloud.MAGIC));self.assertNotIn(b'PK\x03\x04',cipher[:20]);self.assertNotEqual(cipher,self.archive)
  self.assertEqual(client.download('d'*32),self.archive);self.assertEqual(len(client.list()['items']),1)
  with tempfile.TemporaryDirectory() as root:
   destination=Path(root)/'new';client.restore('d'*32,destination)
   self.assertEqual(fixture.semantic_state(destination),self.receipt['expected'])
 def test_recovery_cipher_key_owner_or_id_mismatch_and_byte_tampering_reject(self):
  data=cloud.encrypt_workspace(self.archive,'c'*64,'a'*64,'d'*32)
  for key,owner,identifier,content in [('e'*64,'a'*64,'d'*32,data),('c'*64,'f'*64,'d'*32,data),('c'*64,'a'*64,'1'*32,data),('c'*64,'a'*64,'d'*32,data[:-1]+bytes([data[-1]^1]))]:
   with self.assertRaisesRegex(ValueError,'integrity'):cloud.decrypt_workspace(content,key,owner,identifier)
 def test_default_off_and_missing_recovery_ack_cannot_send_a_backup(self):
  c=config();c.update(enabled=False,recoveryAcknowledged=False);transport=MemoryTransport()
  with self.assertRaisesRegex(ValueError,'enable'):cloud.SiteR2Client(c,transport=transport).upload(self.archive)
  self.assertEqual(transport.calls,[])
  c['enabled']=True
  with self.assertRaisesRegex(ValueError,'recovery key'):cloud.validate_config(c)
 def test_existing_destination_is_not_downloaded_or_overwritten(self):
  transport=MemoryTransport();client=cloud.SiteR2Client(config(),transport=transport)
  with tempfile.TemporaryDirectory() as root:
   target=Path(root)/'keep';target.mkdir();(target/'user.txt').write_text('USER')
   with self.assertRaises(FileExistsError):client.restore('d'*32,target)
   self.assertEqual((target/'user.txt').read_text(),'USER');self.assertEqual(transport.calls,[])
 def test_private_config_and_separate_recovery_key_are_excluded_from_workspace_zip(self):
  with tempfile.TemporaryDirectory() as root:
   folder=Path(root)/'source';workspace_backup.restore_workspace(self.archive,folder)
   status=cloud.save_config(folder,config());self.assertTrue(status['configured']);self.assertNotIn('ownerKey',status)
   self.assertEqual((folder/'cloud-backup-credentials.json').stat().st_mode&0o777,0o600)
   bundle=workspace_backup.export_workspace(folder);manifest,files=workspace_backup._read(bundle)
   self.assertFalse(any('credential' in name for name in files));self.assertNotIn(b'synthetic-service-access-only',b''.join(files.values()))
   recovery=cloud.recovery_bundle(cloud.load_config(folder));self.assertNotIn('serviceBearer',recovery);self.assertNotEqual(recovery['ownerKey'],recovery['cipherKey'])
   (folder/'cloud-backup-credentials.json').chmod(0o644)
   with self.assertRaisesRegex(ValueError,'0600'):cloud.load_config(folder)
 def test_desktop_recovery_download_is_json_data_for_the_http_json_encoder(self):
  with tempfile.TemporaryDirectory() as root:
   cloud.save_config(root,config());status,value,mime,headers=cloud.dispatch(root,'cloud-backup/recovery.json')
   self.assertEqual(status,200);self.assertIsInstance(value,dict);self.assertEqual(value['cipherKey'],'c'*64)
   self.assertNotIn('serviceBearer',value);self.assertIn('application/json',mime);self.assertIn('attachment;',headers['Content-Disposition'])
 def test_wrong_site_redirect_unknown_fields_and_phone_access_reject(self):
  for value in [{**config(),'origin':'https://attacker.invalid'},{**config(),'other':'bad'},{**config(),'cipherKey':'b'*64}]:
   with self.assertRaises(ValueError):cloud.validate_config(value)
  self.assertIsNone(cloud._NoRedirect().redirect_request(None,None,None,None,None,None))
  for path in ('cloud-backup','cloud-backup/upload','cloud-backup/recovery.json'):
   self.assertEqual(cloud.dispatch('/nonexistent',path,namespace='phone')[0],403)
 def test_download_checksum_mismatch_is_detected_before_decryption(self):
  def bad(*args,**kwargs):return 200,{'X-Redcn-Cipher-Sha256':'0'*64},b'bad'
  with self.assertRaisesRegex(ValueError,'checksum'):cloud.SiteR2Client(config(),transport=bad).download('d'*32)
 def test_failed_atomic_key_update_preserves_old_file_and_removes_private_temporary(self):
  with tempfile.TemporaryDirectory() as root:
   cloud.save_config(root,config());before=(Path(root)/'cloud-backup-credentials.json').read_bytes()
   with patch.object(Path,'replace',side_effect=OSError('synthetic disk failure')):
    with self.assertRaises(OSError):cloud.save_config(root,{**config(),'enabled':False})
   self.assertEqual((Path(root)/'cloud-backup-credentials.json').read_bytes(),before)
   self.assertEqual([p.name for p in Path(root).iterdir()],['cloud-backup-credentials.json'])
 def test_lost_registration_response_retries_with_the_same_persisted_capability(self):
  with tempfile.TemporaryDirectory() as root:
   keys=[]
   def failed(client,key):keys.append(client.config['ownerKey']);raise ValueError('synthetic lost response')
   data={'action':'configure','serviceBearer':'synthetic-service-access-only','registrationKey':'f'*64}
   with patch.object(cloud.SiteR2Client,'register',failed):
    with self.assertRaisesRegex(ValueError,'lost response'):cloud.dispatch(root,'cloud-backup',data)
   pending=cloud.load_config(root);self.assertFalse(pending['registered']);self.assertFalse(pending['enabled'])
   def success(client,key):keys.append(client.config['ownerKey']);return {'registered':True}
   with patch.object(cloud.SiteR2Client,'register',success):self.assertTrue(cloud.dispatch(root,'cloud-backup',data)[1]['configured'])
   self.assertEqual(keys[0],keys[1]);self.assertEqual(cloud.load_config(root)['cipherKey'],pending['cipherKey'])
 def test_configure_never_replaces_an_existing_recovery_key(self):
  with tempfile.TemporaryDirectory() as root:
   cloud.save_config(root,config())
   with patch.object(cloud.SiteR2Client,'register') as register:
    with self.assertRaisesRegex(ValueError,'already exist'):cloud.dispatch(root,'cloud-backup',{'action':'configure','serviceBearer':'other-valid-service-access','registrationKey':'f'*64})
    register.assert_not_called()
   self.assertEqual(cloud.load_config(root)['cipherKey'],'c'*64)
if __name__=='__main__':unittest.main()
