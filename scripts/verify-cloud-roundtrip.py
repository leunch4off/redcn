#!/usr/bin/env python3
"""Real private R2 roundtrip of PUBLIC SYNTHETIC fixture only.

Service access and registration secret arrive through hidden stdin, never argv,
source, browser storage, output or a receipt. No operator cache is opened.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from cloud_backup import ORIGIN,SiteR2Client
from portability_proof import verify_fixture

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fixture',required=True);p.add_argument('--receipt-output',required=True);a=p.parse_args()
 try:
  import termios
  if sys.stdin.isatty():
   settings=termios.tcgetattr(sys.stdin);settings[3]&=~termios.ECHO;termios.tcsetattr(sys.stdin,termios.TCSANOW,settings)
 except ImportError:pass
 print('READY_CLOUD_PILOT_JSON (input hidden)',flush=True)
 raw=sys.stdin.readline(20000)
 secret=json.loads(raw)
 if not isinstance(secret,dict) or set(secret)!={'serviceBearer','registrationKey'}:raise ValueError('Provide private Site service access and registration key through stdin only')
 fixture=Path(a.fixture);archive=(fixture/'workspace.zip').read_bytes();receipt=json.loads((fixture/'source.json').read_text())
 if receipt.get('synthetic')is not True or receipt.get('type')!='redcn-public-synthetic-portability':raise ValueError('Only public generated fixtures are allowed')
 source_verified=verify_fixture(archive,receipt)
 config={'version':1,'registered':True,'enabled':True,'origin':ORIGIN,'ownerId':hashlib.sha256(('public-synthetic-pilot-'+secrets.token_hex(32)).encode()).hexdigest(),
  'ownerKey':secrets.token_hex(32),'cipherKey':secrets.token_hex(32),'serviceBearer':secret['serviceBearer'],'recoveryAcknowledged':True}
 client=SiteR2Client(config);client.register(secret['registrationKey']);stored=client.upload(archive)
 listed=client.list()
 if not any(row['id']==stored['id'] and row['sha256']==stored['sha256'] for row in listed['items']):raise ValueError('Cloud listing did not read back the stored ciphertext')
 downloaded=client.download(stored['id'])
 if downloaded!=archive:raise ValueError('Cloud-decrypted original ZIP bytes differ')
 verified=verify_fixture(downloaded,receipt)
 result={'version':1,'type':'redcn-real-cloud-backup-pilot','verified':True,'syntheticOnly':True,'actualNetworkTransport':True,'origin':ORIGIN,
  'ownerId':stored['ownerId'],'backupId':stored['id'],'cipherSha256':stored['sha256'],'cipherBytes':stored['bytes'],'archiveSha256':stored['archiveSha256'],
  'restored':{k:verified[k] for k in ('cards','attachments','versions','photos','verifiedComponents','devicesMustPairAgain','clientMaterialsRequireRegeneration')},
  'credentialsInReceipt':False,'operatorCacheRead':False,'timeUtc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
 out=Path(a.receipt_output)
 if out.exists():raise FileExistsError('Choose a new public pilot receipt file')
 out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n');out.chmod(0o600)
 print(json.dumps(result),flush=True)
if __name__=='__main__':main()
