"""Public generated workspace for a verifiable Mac-to-Mac restore drill.

This module never opens the operator cache. Every input is generated inside a
TemporaryDirectory. Neither the fixture nor receipts contain a hostname, user
name, device key, source photograph, contract, or listing HTML.
"""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import tempfile
import time

import card_assets
import dictionary_reviews
from learning_memory import LearningMemory
from node_runtime import resolve_node
import photo_store
from phone_relay import load_profile, save_profile
import workspace_backup

ROOT=Path(__file__).resolve().parents[1]
URL='https://images.cdn-cian.ru/images/redcn-generated-portability.png'
MARKERS=('redcn-synthetic-portability-a','redcn-synthetic-portability-b')


def _sha(data):return hashlib.sha256(data).hexdigest()


def _picture(color):
    from PIL import Image
    stream=io.BytesIO()
    with Image.new('RGB',(96,72),color) as image:image.save(stream,'PNG')
    return stream.getvalue()


def _template():
    return json.loads(subprocess.check_output([resolve_node(),'--input-type=module','-e',
        'import {defaultPublicationDesign} from "./web/publication-model.mjs";process.stdout.write(JSON.stringify(defaultPublicationDesign()));'],cwd=ROOT,text=True))


def semantic_state(directory):
    """Compare relationships and exact bytes while accepting remapped card IDs."""
    root=Path(directory)
    with sqlite3.connect(root/'listings.sqlite3') as db:
        rows={identifier:json.loads(value) for identifier,value in db.execute('SELECT id,payload FROM results')}
    identities={identifier:row['fields']['editorIdentity'] for identifier,row in rows.items()}
    cards=[]
    for identifier,row in rows.items():
        with card_assets._db(root) as db:
            attachments=[{'name':r[0],'category':r[1],'sha256':_sha(card_assets._read(root,r[2]))}
                for r in db.execute('SELECT name,category,digest FROM attachments WHERE card_id=? ORDER BY name',(identifier,))]
            exports=[{'kind':r[0],'sha256':_sha(card_assets._read(root,r[1]))}
                for r in db.execute('SELECT kind,digest FROM exports WHERE card_id=? ORDER BY kind,digest',(identifier,))]
        cards.append({'marker':identities[identifier],'titles':row['titles'],'body':row['body'],'tags':row['tags'],
            'related':sorted(identities[r['resultId']] for r in row['fields'].get('relatedOffers',[])),
            'attachments':attachments,'exports':exports,'archive':{k:v for k,v in card_assets.archive(root,identifier).items() if k in ('status','notes')}})
    selected=photo_store.accepted_photo_for_url(root,URL)
    original=photo_store.asset(root,selected['original']['id'])[0]
    prepared=photo_store.asset(root,selected['selected']['id'])[0]
    return {'cards':sorted(cards,key=lambda r:r['marker']),
        'originalSha256':_sha(original),'preparedSha256':_sha(prepared),
        'template':card_assets.load_design(root)['template'],'profile':load_profile(root),
        'dictionary':dictionary_reviews.snapshot(root)['rules'],
        'learning':LearningMemory(root).status()['counts']}


def generate_fixture(*, require_macos=False, origin='development-test'):
    if require_macos and platform.system()!='Darwin':raise ValueError('The source fixture must be exported on macOS')
    if origin not in ('development-test','operator-mac'):raise ValueError('Unknown source origin')
    if origin=='operator-mac' and (not require_macos or os.environ.get('GITHUB_ACTIONS')=='true'):
        raise ValueError('operator-mac requires actual non-GitHub macOS execution')
    with tempfile.TemporaryDirectory(prefix='redcn-generated-portability-') as temp:
        root=Path(temp)/'source';root.mkdir()
        one,two='a'*32,'b'*32
        with sqlite3.connect(root/'listings.sqlite3') as db:
            db.execute('CREATE TABLE results(id TEXT PRIMARY KEY,updated REAL,payload TEXT)')
            for identifier,other,marker in ((one,two,MARKERS[0]),(two,one,MARKERS[1])):
                card={'fields':{'editorIdentity':marker,'price':'80000','area':'40','photos':[URL] if identifier==one else [],'relatedOffers':[{'resultId':other}]},
                    'titles':['合成测试房源'],'body':'仅供备份恢复测试，不是真实房源。','tags':'#合成测试'}
                db.execute('INSERT INTO results VALUES(?,?,?)',(identifier,1,json.dumps(card,ensure_ascii=False)))
        original=photo_store.attach_photo(root,URL,'data:image/png;base64,'+base64.b64encode(_picture('#8899aa')).decode())
        photo_store.crop_photo(root,original['id'],{'left':5,'right':3})
        for name,category,data in [('synthetic-contract.pdf','contract',b'%PDF-1.4\nSYNTHETIC GENERATED FIXTURE\n%%EOF'),
                ('synthetic-floorplan.png','floorplan',_picture('#335577')),
                ('synthetic-lidar.obj','lidar',b'# generated triangle, not an apartment scan\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')]:
            card_assets.add_attachment(root,one,{'name':name,'category':category,'data':base64.b64encode(data).decode()})
        card_assets.update_archive(root,one,{'status':'rented','notes':'GENERATED SYNTHETIC NOTE'})
        template=_template();card_assets.save_design(root,template)
        for index in range(2):
            version=copy.deepcopy(template);version['theme']['pdf']['accent']='#bb8833' if index else '#aa9922'
            card_assets.create_export(root,one,'<!doctype html><html><body>合成版本'+str(index)+'</body></html>','html',template=version)
        save_profile(root,{'rules':{'письменный стол':'书桌'},'prefs':{'brandTags':'#合成测试'},'data':{'style':'compact'}})
        state=dictionary_reviews.snapshot(root)
        state=dictionary_reviews.mutate(root,{'action':'propose','expectedRevision':state['revision'],'ru':'рабочий стол','zh':'书桌','source':'Generated portability fixture','origin':'operator'},actor='synthetic-editor')
        dictionary_reviews.mutate(root,{'action':'approve','expectedRevision':state['revision'],'id':state['entries'][0]['id']},actor='synthetic-reviewer')
        memory=LearningMemory(root)
        memory.record('translation',{'ru':'есть стол','zh':'有书桌'},status='approved',group_id='generated-one')
        memory.record('translation',{'ru':'проверить мебель','zh':'核实家具'},group_id='generated-two')
        expected=semantic_state(root);content=workspace_backup.export_workspace(root)
    receipt={'version':1,'type':'redcn-public-synthetic-portability','synthetic':True,'source':{'system':platform.system(),'machine':platform.machine(),'macos':platform.mac_ver()[0] or None,'origin':origin},
        'exporterSha256':_sha(Path(__file__).read_bytes()),'archiveSha256':_sha(content),'archiveBytes':len(content),
        'createdUtc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'expected':expected,
        'scope':'Generated pixels/text/documents only; no operator cache was read'}
    return content,receipt


def verify_fixture(content,receipt,*, require_github_macos=False):
    if not isinstance(receipt,dict) or receipt.get('type')!='redcn-public-synthetic-portability' or receipt.get('version')!=1 or receipt.get('synthetic') is not True:
        raise ValueError('Only the generated public fixture is accepted')
    if receipt.get('archiveSha256')!=_sha(content) or receipt.get('archiveBytes')!=len(content):raise ValueError('Fixture archive hash/size mismatch')
    github_hosted=(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted')
    if require_github_macos:
        if platform.system()!='Darwin' or not github_hosted or os.environ.get('RUNNER_OS')!='macOS':
            raise ValueError('The destination must be a real GitHub-hosted macOS runner')
        if receipt.get('source',{}).get('system')!='Darwin' or receipt.get('source',{}).get('origin')!='operator-mac':
            raise ValueError('The source must be the separately exported operator Mac fixture')
    inspected=workspace_backup.inspect_workspace(content)
    with tempfile.TemporaryDirectory(prefix='redcn-portability-destination-') as temp:
        destination=Path(temp)/'fresh-workspace'
        restored=workspace_backup.restore_workspace(content,destination)
        if semantic_state(destination)!=receipt.get('expected'):raise ValueError('Restored library/media/dictionary/design differs from source')
        ids=[r['result_id'] for r in restored['cards']]
        if any(i in ('a'*32,'b'*32) for i in ids):raise ValueError('Card IDs were not remapped')
        for identifier in ids:
            if any(not row.get('requiresRegeneration') for row in card_assets.list_exports(destination,identifier)):
                raise ValueError('Historical client exports must be marked for regeneration')
        excluded=('phone-access.json','cookies.json','import-diagnostics','models')
        if any((destination/name).exists() for name in excluded):raise ValueError('Runtime/private state leaked into restored fixture')
    return {'version':1,'type':'redcn-portability-verification','verified':True,'archiveSha256':_sha(content),
        'source':receipt['source'],'destination':{'system':platform.system(),'machine':platform.machine(),'macos':platform.mac_ver()[0] or None,'githubHosted':github_hosted,'runId':os.environ.get('GITHUB_RUN_ID')},
        'cards':inspected['cards'],'attachments':inspected['attachments'],'versions':inspected['exports'],'photos':inspected['photos'],
        'verifiedComponents':['card-relations','attachment-bytes','original-and-cropped-photo-bytes','template','material-version-bytes','archive-status','profile','approved-dictionary','learning-counts'],
        'devicesMustPairAgain':True,'clientMaterialsRequireRegeneration':True}
