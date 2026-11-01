"""Explicit owner-only encrypted cloud backups through the private Site's R2.

No automatic upload or credential discovery. Configuration is a private 0600
file, excluded from portable bundles. A distinct recovery cipher key never
leaves the Mac; the cloud owner capability is not the cipher key.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import struct
import tempfile
import urllib.error
import urllib.request

import workspace_backup

ORIGIN='https://petrov-rednote-studio.vichnewka.chatgpt.site'
MAX_CIPHER=64*1024*1024
MAGIC=b'REDCN-CLOUD-1\n'
_HEX=re.compile(r'[a-f0-9]{64}')
_ID=re.compile(r'[a-f0-9]{32}')


def _sha(data):return hashlib.sha256(data).hexdigest()


def _aes():
 try:from cryptography.hazmat.primitives.ciphers.aead import AESGCM
 except ImportError as error:raise ValueError('Install cli/requirements-cloud.txt to use encrypted cloud backups') from error
 return AESGCM


def encrypt_workspace(content,cipher_key,owner,identifier):
 if not _HEX.fullmatch(str(cipher_key)) or not _HEX.fullmatch(str(owner)) or not _ID.fullmatch(str(identifier)):raise ValueError('Invalid backup keys or identity')
 if not isinstance(content,bytes) or len(content)>MAX_CIPHER-4096:raise ValueError('Cloud copies are limited to 64 MiB')
 workspace_backup.inspect_workspace(content)
 nonce=secrets.token_bytes(12);aad=('redcn-cloud-v1:'+owner+':'+identifier).encode()
 meta=json.dumps({'version':1,'archiveSha256':_sha(content),'archiveBytes':len(content)},sort_keys=True).encode()
 plain=struct.pack('!I',len(meta))+meta+content
 encrypted=MAGIC+nonce+_aes()(bytes.fromhex(cipher_key)).encrypt(nonce,plain,aad)
 if len(encrypted)>MAX_CIPHER:raise ValueError('Cloud copies are limited to 64 MiB; retain the full local ZIP for larger workspaces')
 return encrypted


def decrypt_workspace(content,cipher_key,owner,identifier):
 if not isinstance(content,bytes) or not len(MAGIC)+28<=len(content)<=MAX_CIPHER or not content.startswith(MAGIC):raise ValueError('Invalid encrypted backup')
 if not _HEX.fullmatch(str(cipher_key)) or not _HEX.fullmatch(str(owner)) or not _ID.fullmatch(str(identifier)):raise ValueError('Invalid backup keys or identity')
 offset=len(MAGIC);nonce=content[offset:offset+12]
 try:plain=_aes()(bytes.fromhex(cipher_key)).decrypt(nonce,content[offset+12:],('redcn-cloud-v1:'+owner+':'+identifier).encode())
 except Exception as error:raise ValueError('Recovery key or encrypted backup integrity did not match') from error
 if len(plain)<5:raise ValueError('Invalid encrypted metadata')
 size=struct.unpack('!I',plain[:4])[0]
 if not 1<=size<=1000:raise ValueError('Invalid encrypted metadata')
 try:meta=json.loads(plain[4:4+size])
 except (ValueError,UnicodeError) as error:raise ValueError('Invalid encrypted metadata') from error
 archive=plain[4+size:]
 if meta!={'version':1,'archiveSha256':_sha(archive),'archiveBytes':len(archive)}:raise ValueError('Decrypted archive hash mismatch')
 workspace_backup.inspect_workspace(archive)
 return archive


def validate_config(value):
 keys={'version','enabled','origin','ownerId','ownerKey','cipherKey','serviceBearer','recoveryAcknowledged','registered'}
 if not isinstance(value,dict) or set(value)!=keys or value.get('version')!=1 or value.get('origin')!=ORIGIN:raise ValueError('Use the connected private redcn Site only')
 if type(value.get('enabled'))is not bool or type(value.get('recoveryAcknowledged'))is not bool or type(value.get('registered'))is not bool:raise ValueError('Explicit cloud and recovery-key choices are required')
 if not all(_HEX.fullmatch(str(value.get(k,''))) for k in ('ownerId','ownerKey','cipherKey')) or value['ownerKey']==value['cipherKey']:raise ValueError('Use distinct cloud capability and recovery cipher keys')
 token=value.get('serviceBearer')
 if not isinstance(token,str) or not 20<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('Private Site service access is required')
 if value['enabled'] and not value['registered']:raise ValueError('Register this cloud owner before enabling upload')
 if value['enabled'] and not value['recoveryAcknowledged']:raise ValueError('Save and confirm the separate recovery key before enabling upload')
 return dict(value)


def save_config(directory,value):
 value=validate_config(value);root=Path(directory)
 if root.is_symlink():raise ValueError('Cloud workspace must not be a symlink')
 root.mkdir(parents=True,exist_ok=True,mode=0o700);path=root/'cloud-backup-credentials.json'
 if path.is_symlink():raise ValueError('Cloud credential file must not be a symlink')
 temporary=None
 try:
  with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=root,delete=False) as out:
   temporary=Path(out.name);temporary.chmod(0o600);json.dump(value,out);out.flush();os.fsync(out.fileno())
  temporary.replace(path)
 finally:
  if temporary is not None:temporary.unlink(missing_ok=True)
 return public_status(value)


def load_config(directory):
 path=Path(directory)/'cloud-backup-credentials.json'
 if path.is_symlink() or not path.is_file() or not 1<=path.stat().st_size<=16000 or path.stat().st_mode&0o077:raise ValueError('Private 0600 cloud configuration is not available')
 return validate_config(json.loads(path.read_text()))


def public_status(config):return {'version':1,'configured':config['registered'],'enabled':config['enabled'],'origin':config['origin'],'ownerId':config['ownerId'],'recoveryAcknowledged':config['recoveryAcknowledged'],'maxCipherBytes':MAX_CIPHER}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None


class SiteR2Client:
 def __init__(self,config,*,transport=None):
  self.config=validate_config(config)
  self.transport=transport or self._request
 def _request(self,method,path,data=None,extra=None):
  if not re.fullmatch(r'/api/cloud-backup/v1/owners/[a-f0-9]{64}(?:/backups(?:/[a-f0-9]{32})?)?',path):raise ValueError('Invalid cloud operation path')
  headers={'OAI-Sites-Authorization':'Bearer '+self.config['serviceBearer'],'X-Redcn-Backup-Key':self.config['ownerKey'],'Accept':'application/json',**(extra or {})}
  if data is not None:headers['Content-Length']=str(len(data))
  try:
   import certifi
   context=ssl.create_default_context(cafile=certifi.where())
  except ImportError:context=ssl.create_default_context()
  opener=urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=context))
  try:
   with opener.open(urllib.request.Request(self.config['origin']+path,data=data,headers=headers,method=method),timeout=40) as response:
    content=response.read(MAX_CIPHER+1)
    if len(content)>MAX_CIPHER:raise ValueError('Cloud response exceeds 64 MiB')
    return response.status,dict(response.headers),content
  except urllib.error.HTTPError as error:raise ValueError('Private cloud returned HTTP '+str(error.code)) from None
  except (urllib.error.URLError,TimeoutError,ssl.SSLError):raise ValueError('Private cloud connection failed; local ZIP and recovery key are unchanged') from None
 def register(self,registration_key):
  if not _HEX.fullmatch(str(registration_key)):raise ValueError('Owner registration authorization is required')
  status,_,body=self.transport('POST',self.base,data=b'{}',extra={'Content-Type':'application/json','X-Redcn-Registration-Key':registration_key})
  value=json.loads(body)
  if status not in (200,201) or value.get('ownerId')!=self.config['ownerId'] or value.get('registered')is not True:raise ValueError('Cloud did not confirm this owner registration')
  return {'registered':True,'ownerId':self.config['ownerId']}
 @property
 def base(self):return '/api/cloud-backup/v1/owners/'+self.config['ownerId']
 def upload(self,archive,identifier=None):
  if not self.config['enabled'] or not self.config['recoveryAcknowledged']:raise ValueError('Explicitly enable cloud copying after saving the recovery key')
  identifier=identifier or secrets.token_hex(16)
  encrypted=encrypt_workspace(archive,self.config['cipherKey'],self.config['ownerId'],identifier)
  sha=_sha(encrypted)
  status,_,body=self.transport('PUT',self.base+'/backups/'+identifier,data=encrypted,extra={'Content-Type':'application/octet-stream','X-Redcn-Cipher-Sha256':sha})
  value=json.loads(body)
  expected={'id':identifier,'ownerId':self.config['ownerId'],'sha256':sha,'bytes':len(encrypted),'stored':True}
  if status not in (200,201) or any(value.get(k)!=v for k,v in expected.items()):raise ValueError('Cloud did not confirm exact encrypted bytes')
  return {'version':1,**expected,'archiveSha256':_sha(archive)}
 def list(self):
  status,_,body=self.transport('GET',self.base+'/backups')
  value=json.loads(body)
  if status!=200 or value.get('ownerId')!=self.config['ownerId'] or not isinstance(value.get('items'),list) or len(value['items'])>50:raise ValueError('Invalid cloud backup list')
  if any(not isinstance(row,dict) or not _ID.fullmatch(str(row.get('id',''))) or not _HEX.fullmatch(str(row.get('sha256',''))) or type(row.get('bytes'))is not int or not 1<=row['bytes']<=MAX_CIPHER for row in value['items']):raise ValueError('Invalid cloud backup metadata')
  return value
 def download(self,identifier):
  if not _ID.fullmatch(str(identifier)):raise ValueError('Invalid cloud backup ID')
  status,headers,encrypted=self.transport('GET',self.base+'/backups/'+identifier)
  headers={k.lower():v for k,v in headers.items()}
  if status!=200 or headers.get('x-redcn-cipher-sha256')!=_sha(encrypted):raise ValueError('Cloud download checksum mismatch')
  return decrypt_workspace(encrypted,self.config['cipherKey'],self.config['ownerId'],identifier)
 def restore(self,identifier,destination):
  target=Path(destination)
  if target.exists() or target.is_symlink():raise FileExistsError('Restore only into a new directory')
  return workspace_backup.restore_workspace(self.download(identifier),target)


def recovery_bundle(config):
 config=validate_config(config)
 return {k:config[k] for k in ('version','origin','ownerId','ownerKey','cipherKey')}


def dispatch(directory,path,data=None,*,namespace='local'):
 if path not in ('cloud-backup','cloud-backup/upload','cloud-backup/list','cloud-backup/restore','cloud-backup/recovery.json'):return None
 if namespace!='local':return 403,{'error':'Cloud workspace backups require the desktop owner'},'application/json; charset=utf-8',{}
 if path=='cloud-backup' and data is None:
  try:value=public_status(load_config(directory))
  except (OSError,ValueError):value={'version':1,'configured':False,'enabled':False,'origin':ORIGIN,'maxCipherBytes':MAX_CIPHER}
 elif path=='cloud-backup' and isinstance(data,dict):
  if data.get('action')=='configure':
   if set(data)!={'action','serviceBearer','registrationKey'}:raise ValueError('Unknown cloud configuration fields')
   if (Path(directory)/'cloud-backup-credentials.json').exists():
    config=load_config(directory)
    if config['registered']:raise ValueError('Cloud keys already exist; keep the recovery file and refresh service access instead')
    config={**config,'serviceBearer':data['serviceBearer']}
   else:
    config={'version':1,'registered':False,'enabled':False,'origin':ORIGIN,'ownerId':secrets.token_hex(32),'ownerKey':secrets.token_hex(32),'cipherKey':secrets.token_hex(32),'serviceBearer':data['serviceBearer'],'recoveryAcknowledged':False}
   # Persist the private provisional keys BEFORE a remote registration can
   # succeed. A lost response/disk error can then be retried with the same key.
   save_config(directory,config)
   SiteR2Client(config).register(data['registrationKey']);value=save_config(directory,{**config,'registered':True})
  elif data.get('action')=='refresh-access':
   if set(data)!={'action','serviceBearer'}:raise ValueError('Unknown service access fields')
   config={**load_config(directory),'serviceBearer':data['serviceBearer']};SiteR2Client(config).list();value=save_config(directory,config)
  elif data.get('action')=='enable':
   if set(data)!={'action','enabled','recoveryAcknowledged'}:raise ValueError('Unknown cloud enable fields')
   config=load_config(directory);value=save_config(directory,{**config,'enabled':data['enabled'],'recoveryAcknowledged':data['recoveryAcknowledged']})
  else:raise ValueError('Unknown cloud configuration action')
 else:
  config=load_config(directory);client=SiteR2Client(config)
  if path=='cloud-backup/upload' and data=={}:value=client.upload(workspace_backup.export_workspace(directory))
  elif path=='cloud-backup/list' and data is None:value=client.list()
  elif path=='cloud-backup/restore' and isinstance(data,dict) and set(data)=={'id','destination'}:value=client.restore(data['id'],data['destination'])
  elif path=='cloud-backup/recovery.json' and data is None:return 200,recovery_bundle(config),'application/json',{'Content-Disposition':'attachment; filename="redcn-cloud-recovery-key.json"'}
  else:raise ValueError('Invalid cloud backup operation')
 return 200,value,'application/json; charset=utf-8',{}


def main():
 parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
 for action in ('upload','list','restore'):
  cmd=sub.add_parser(action);cmd.add_argument('--config',required=True);cmd.add_argument('--archive' if action=='upload' else '--id',required=action!='list');
  if action=='restore':cmd.add_argument('--destination',required=True)
 args=parser.parse_args();config_path=Path(args.config)
 if config_path.is_symlink() or not config_path.is_file() or not 0<config_path.stat().st_size<=16000 or config_path.stat().st_mode&0o077:raise ValueError('Use a private 0600 configuration file')
 client=SiteR2Client(json.loads(config_path.read_text()))
 if args.action=='upload':result=client.upload(Path(args.archive).read_bytes())
 elif args.action=='list':result=client.list()
 else:result=client.restore(args.id,args.destination)
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
