#!/usr/bin/env python3
"""Actual isolated desktop/phone Chrome workflow, with optional native AI calls.

The fixture contains anonymized source structure and synthetic images. Chrome
at phone size is explicitly not Safari/iPhone acceptance. Native model output
is recorded as accepted, rejected or fallback; never silently called new truth.
"""
from contextlib import contextmanager
import argparse
import copy
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import sys
import threading
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'));sys.path.insert(0,str(ROOT/'tests'))
import local_server
import redcn
import photo_store
import photo_instances
import card_assets
import dossier_backup
from project_soak import atomic
from source_adapters import adapter_for

URL='https://www.cian.ru/rent/flat/123456789/'


def _inventory(root):
    result={}
    for name,tables in (('listings.sqlite3',('results',)),('media.sqlite3',('assets',)),
                        ('card-assets/dossiers.sqlite3',('exports','attachments'))):
        path=root/name
        if not path.is_file() or path.is_symlink():continue
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        try:
            for table in tables:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    result[name+':'+table]=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        finally:db.close()
    return result


def _export_state(cache,identifier):
    """Hash-only diagnostic of the exact private fixture, never field values.

    Keyed hashes permit comparisons without making low-entropy private fields
    guessable from a published receipt. The key itself stays in the fixture.
    """
    from client_bundle_consistency import snapshot
    from fact_ledger import NUMERIC_FIELDS,BOOLEAN_FIELDS,LIST_FIELDS,MAP_FIELDS,TEXT_FIELDS
    revision=snapshot(cache,identifier)
    key=(cache/'card-assets/revision-key').read_bytes()
    def digest(value):
        encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        return hmac.new(key,b'workflow-diagnostic-v1\0'+encoded,hashlib.sha256).hexdigest()
    def read_db(relative,query,params=()):
        path=cache/relative
        if not path.is_file() or path.is_symlink():return []
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
            db.execute('PRAGMA query_only=ON')
            return db.execute(query,params).fetchall()
    rows=read_db('listings.sqlite3','SELECT payload,updated FROM results WHERE id=?',(identifier,))
    if not rows:raise AssertionError('Isolated card disappeared')
    card=json.loads(rows[0][0]);fields=card.get('fields',{})
    allowed=set(NUMERIC_FIELDS)|set(BOOLEAN_FIELDS)|set(LIST_FIELDS)|set(MAP_FIELDS)|set(TEXT_FIELDS)
    sections={name:digest(card.get(name)) for name in ('titles','body','tags','listing')}
    sections['otherTopLevel']=digest({name:value for name,value in card.items() if name not in {'fields','titles','body','tags','listing'}})
    for name in sorted(allowed&fields.keys()):sections['fields.'+name]=digest(fields[name])
    sections['fields.other']=digest({name:value for name,value in fields.items() if name not in allowed})
    edits=fields.get('photoEdits',{})
    if isinstance(edits,dict):
        for name in ('order','excluded','originals','crops','observations','reviewJournal'):
            sections['fields.photoEdits.'+name]=digest(edits.get(name))
        observations=edits.get('observations',{})
        if isinstance(observations,dict):
            ordered=[value for _,value in sorted(observations.items()) if isinstance(value,dict)]
            for name in ('confirmed','rejected'):
                sections['fields.photoEdits.observations.'+name]=digest([value.get(name) for value in ordered])
            for name in ('status','provider','engine','model','originalId','candidates','autoApply','fallback','cached','ai','message','limitations','createdAt','checkedAt'):
                sections['fields.photoEdits.observations.analysis.'+name]=digest([value.get('analysis',{}).get(name) for value in ordered if isinstance(value.get('analysis'),dict)])
    media={}
    media_queries={'assets':'SELECT id,mime,metadata FROM assets ORDER BY id',
                   'photo_urls':'SELECT url,id FROM photo_urls ORDER BY url',
                   'crop_links':'SELECT original_id,asset_id,verification,metadata FROM crop_links ORDER BY original_id,asset_id,verification'}
    tables={row[0] for row in read_db('media.sqlite3',"SELECT name FROM sqlite_master WHERE type='table'")}
    for name,query in media_queries.items():
        if name in tables:
            values=read_db('media.sqlite3',query);media[name]={'rows':len(values),'hmacSha256':digest(values)}
    exports=card_assets.list_exports(cache,identifier)
    return {'cardRevisionSha256':revision,'stableRead':snapshot(cache,identifier)==revision,'capturedAt':time.time(),'cardHmacSha256':digest(card),'savedAt':rows[0][1],
            'sections':sections,'media':media,
            'exports':[{'kind':row['kind'],'created':row['created'],'revisionSha256':row['revisionSha256'],
                        'revisionStatus':row['revisionStatus'],'requiresRegeneration':row['requiresRegeneration']} for row in exports]}


@contextmanager
def _resource(root):
    """Yield to an existing model owner and stop on new production activity."""
    from learning_scheduler import model_resource
    def active_imports():
        path=root/'import-diagnostics/campaigns.sqlite3'
        if not path.exists():return 0
        if path.is_symlink():return None
        try:
            db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
            try:return db.execute('SELECT COUNT(*) FROM attempts WHERE finished IS NULL').fetchone()[0]
            finally:db.close()
        except (OSError,sqlite3.Error):return None
    if active_imports()!=0:raise ValueError('Production import is active or unavailable; retry this isolated check later')
    marker=root/'learning/foreground.json'
    before=marker.read_bytes() if marker.is_file() and not marker.is_symlink() else None
    stopped=threading.Event()
    def watch():
        while not stopped.wait(.2):
            try:
                now=marker.read_bytes() if marker.is_file() and not marker.is_symlink() else None
                busy=active_imports()
                if now!=before or busy!=0:
                    os.kill(os.getpid(),signal.SIGTERM);return
            except OSError:continue
    with model_resource(root,kind='background',timeout=0):
        thread=threading.Thread(target=watch,daemon=True);thread.start()
        try:yield
        finally:stopped.set();thread.join(1)


def _native(cache,production,args,card,asset):
    from trained_photo_detector import analyze,MODEL_SHA
    from local_text_model import settings
    from local_content import generate_preview
    content,mime,_=photo_store.asset(cache,asset['id'])
    # The application runner supplies Playwright; the established photo worker
    # selects its verified ARM interpreter without adding ORT to that runner.
    analysis=analyze(content,mime,production)
    if analysis.get('status')!='review':raise AssertionError('Native photo detector did not complete')
    config=settings(production)
    selected=args.candidate_adapter or config.get('adapterPath')
    value={'enabled':True,'advertisingEnabled':True,'modelPath':config['modelPath'],
           'pythonPath':config['pythonPath'],'maxSeconds':55,'adapterPath':str(selected) if selected else None,
           'useCandidateAdapter':bool(selected)}
    (cache/'text-ai.json').write_text(json.dumps(value))
    events=[]
    def report(_message,**fields):
        if isinstance(fields.get('ai'),dict):events.append(fields['ai'])
    try:
        preview=generate_preview(cache,{'fields':card['fields'],'styleMode':'advertising',
            'groupId':'isolated-v29-workflow','output':{'body':card['body'],'title':card['titles'][0]}},status=report)
    finally:(cache/'text-ai.json').write_text(json.dumps({'enabled':False,'advertisingEnabled':False}))
    adapter=Path(selected)/'adapters.safetensors' if selected else None
    weights=hashlib.sha256(adapter.read_bytes()).hexdigest() if adapter and adapter.is_file() and adapter.stat().st_size<=10_000_000 else None
    return analysis,preview,{'executed':True,'modelCallsMocked':False,
        'photo':{'modelSha256':MODEL_SHA,'originalSha256':analysis['originalId'],'candidateCount':len(analysis.get('candidates',[])),
                 'inferenceSeconds':analysis.get('ai',{}).get('inferenceSeconds'),
                 'runtime':analysis.get('runtime',{}),'accuracyEvaluated':False},
        'text':{'adapterSha256':weights,'status':preview.get('status'),'fallback':preview.get('fallback'),
                'acceptedDirectModelDraft':preview.get('status')=='review' and preview.get('fallback') is False,
                'elapsedSeconds':preview.get('elapsedSeconds'),'metrics':events[-1] if events else {}}}


def _wait_job(server,identifier):
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        value=server.job_status(identifier)
        if value.get('state')!='running':
            if value.get('state')!='done':raise AssertionError('Isolated cached import failed: '+str(value.get('state')))
            return value
        time.sleep(.03)
    raise AssertionError('Isolated cached import exceeded 35 seconds')


def workflow(fixture,args,receipt):
    from photo_ai_settings import DEFAULTS
    from local_content import generate_preview
    cache=fixture.directory;controller=fixture.controller;page=fixture.page
    (cache/'photo-ai.json').write_text(json.dumps(DEFAULTS))
    (cache/'text-ai.json').write_text(json.dumps({'enabled':False,'advertisingEnabled':False}))
    source=ROOT/'tests/fixtures/cian-user-observed-v26.html'
    listing=redcn.parse_html(source.read_text(),URL)
    listing['images']=[item['sourceUrl'] for item in fixture.assets]
    listing.setdefault('fields',{})['photos']=listing['images']
    listing['parserVersion']=adapter_for(URL)['parserVersion']
    # Structure is the saved observed capture; all apartment values/images are
    # already anonymized or deliberately fictional. No site is requested.
    with redcn.open_cache(cache) as db:db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(URL,time.time(),json.dumps(listing)))
    params={'url':URL,'loader':'http','requestId':'isolated-workflow-v29-001',
            'rules':{'Тестовая':'测试站'},'prefs':{'benefits':'','brandTags':'','contact':''},
            'data':{'registration':'none','pickup':False,'arrival':False,'support':False,'luggage':False}}
    initial=controller.import_diagnostics.status()
    job_id=controller.start_import(params);job=_wait_job(controller,job_id);identifier=job['result_id']
    assert controller.start_import(params)==job_id
    card=local_server.read_result(cache,identifier)
    assert float(card['fields']['price'])==80000 and float(card['fields']['area'])==54
    receipt['import']={'completed':True,'requestReplayedWithoutNewCard':True,
        'sourceFixtureSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'sourceOrigin':'anonymized-observed-dom',
        'externalSourceRequested':False,'readiness':card.get('listing',{}).get('sourceReadiness',{}).get('state')}
    if args.native_ai:
        analysis,preview,receipt['nativeAI']=_native(cache,args.production_cache,args,card,fixture.assets[0])
        card['fields'].setdefault('photoEdits',{}).setdefault('observations',{})[fixture.assets[0]['sourceUrl']]={'analysis':analysis,'confirmed':{},'rejected':{}}
    else:
        preview=generate_preview(cache,{'fields':card['fields'],'styleMode':'advertising','groupId':'isolated-v29-workflow'})
        receipt['nativeAI']={'executed':False,'reason':'not-requested','modelCallsMocked':False}
    # The synthetic image is a flat colour. This deliberately rejected fixture
    # observation proves persistence of an operator decision, not real labeling.
    document={'version':1,'listingId':identifier,'observations':[{'id':'negative-fixture',
        'photoId':fixture.assets[0]['id'],'feature':'piano','region':[.1,.1,.5,.5],
        'status':'rejected','instanceId':None,'linkConfirmed':False}],'countConfirmations':{}}
    reviewed=photo_instances.save_review(cache,identifier,document,photo_ids=[item['id'] for item in fixture.assets],expected_revision=0,actor='isolated scripted operator fixture')
    assert reviewed['document']['observations'][0]['status']=='rejected'
    assert not reviewed['summary']['confirmedVisibleCounts']
    receipt['operatorDecision']={'origin':'scripted-synthetic-negative','recorded':'rejected','revision':reviewed['revision'],'usedForProductionTraining':False}
    assert preview.get('status')=='review', 'No safe advertisement draft was available'
    card.update(body=preview['body'],titles=[preview['title']],tags=preview['tags'])
    card['aiAdvertisement']={key:preview[key] for key in ('status','styleId','fallback','factsHash') if key in preview}
    local_server.save_result(cache,card,identifier)
    receipt['advertisement']={'status':preview['status'],'fallback':preview.get('fallback'),'manuallyAcceptedForFixture':True}

    desktop_thread=threading.Thread(target=controller.serve_forever,daemon=True);desktop_thread.start()
    base='http://127.0.0.1:'+str(controller.server_port)
    blocked=[]
    def network_guard(route):
        url=route.request.url
        if url.startswith((base+'/',fixture.base+'/','data:','blob:')):route.continue_()
        else:blocked.append(True);route.abort()
    fixture.context.route('**/*',network_guard)
    try:
        page.set_viewport_size({'width':1440,'height':1100});page.set_default_timeout(20000)
        page.goto(controller.desktop_access.launch_url(controller.server_port,identifier))
        page.wait_for_function("document.getElementById('output').value.length>20")
        original_body=page.locator('#output').input_value();manual=original_body+'\n咨询看房安排。'
        page.locator('#output').fill(manual);page.locator('#output').dispatch_event('change')
        with page.expect_response(lambda response:response.url.endswith('/api/local/save') and response.request.method=='POST') as saved:
            page.locator('#saveLocal').click()
        assert saved.value.status==200
        page.wait_for_function("document.getElementById('localSaveStatus').textContent.includes('Сохранено')")
        page.reload();page.wait_for_function("document.getElementById('output').value.includes('咨询看房安排')")
        assert page.locator('#output').input_value()==manual
        page.locator('#openPresentation').click()
        page.wait_for_function("document.getElementById('presentationMount').dataset.photosReady==='true'")
        exports=[]
        receipt['exportTimeline']=[]
        def export_state(stage):
            state=_export_state(cache,identifier)
            previous=receipt['exportTimeline'][-1] if receipt['exportTimeline'] else None
            state['stage']=stage
            if previous:
                state['changedSections']=[name for name in sorted(previous['sections'].keys()|state['sections'].keys()) if previous['sections'].get(name)!=state['sections'].get(name)]
                state['changedMediaTables']=[name for name in sorted(previous['media'].keys()|state['media'].keys()) if previous['media'].get(name)!=state['media'].get(name)]
            receipt['exportTimeline'].append(state)
        def download(selector,name,timeout=30000):
            export_state('before-'+name)
            with page.expect_download(timeout=timeout) as event:page.locator(selector).click()
            event.value.save_as(str(args.workspace/name))
            export_state('after-'+name)
        download('#presentationHtml','card.html')
        page.locator('#presentationTabintro').click()
        page.wait_for_function("document.getElementById('presentationImagePreview').naturalWidth===1080")
        download('#presentationImageDownload','intro.png')
        if args.pdf:
            page.locator('#presentationTabpdf').click();download('#presentationPdf','card.pdf',100000)
            assert (args.workspace/'card.pdf').read_bytes().startswith(b'%PDF-')
        exports=card_assets.list_exports(cache,identifier)
        assert all(row['revisionStatus']=='current' and not row['requiresRegeneration'] for row in exports)
        selection={'attachmentIds':[],'exportIds':[row['id'] for row in exports]}
        bundle=dossier_backup.export_client(cache,identifier,selection)
        (args.workspace/'client.zip').write_bytes(bundle)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            manifest=json.loads(archive.read('manifest.json'));assert manifest['cardRevisionSha256']==exports[0]['revisionSha256']
            assert '咨询看房安排' in archive.read('post.txt').decode()
            assert not any('notes' in name or 'contract' in name for name in archive.namelist())
        page.screenshot(path=str(args.workspace/'desktop.png'),full_page=True)

        # Editing a completed package keeps the old bytes, labels them stale,
        # and prevents a mixed-revision ZIP. A new HTML uses the new revision.
        page.locator('nav [data-tab="editor"]').click()
        final_body=manual+'\n手动修改保持。'
        page.locator('#output').fill(final_body);page.locator('#output').dispatch_event('change')
        with page.expect_response(lambda response:response.url.endswith('/api/local/save') and response.request.method=='POST') as saved:
            page.locator('#saveLocal').click()
        assert saved.value.status==200
        assert local_server.read_result(cache,identifier)['body']==final_body
        stale=card_assets.list_exports(cache,identifier)
        assert len(stale)==len(exports) and all(row['requiresRegeneration'] for row in stale)
        try:dossier_backup.export_client(cache,identifier,selection)
        except ValueError:pass
        else:raise AssertionError('Old client package was accepted after an edit')
        page.locator('#openPresentation').click();download('#presentationHtml','card-current.html')
        current=card_assets.list_exports(cache,identifier)
        assert sum(not row['requiresRegeneration'] for row in current)==1
        receipt['clientBundle']={'formats':['html','intro-png']+(['pdf'] if args.pdf else []),
            'sameRevisionBeforeEdit':True,'zipSameRevision':True,'staleFilesPreserved':len(stale),
            'mixedRevisionRejected':True,'currentFilesAfterEdit':1}

        page.locator('nav [data-tab="settings"]').click()
        diagnostic=page.locator('details[data-release-diagnostics="1"]')
        diagnostic.locator('summary').click()
        page.wait_for_function("document.querySelector('[data-release-diagnostics-status]').textContent.includes('соответствуют одной сборке')")
        diagnostic.get_by_role('button',name='Состав диагностического пакета',exact=True).click()
        diagnostic.get_by_role('button',name='Скачать пакет',exact=True).wait_for()
        with page.expect_download() as event:
            diagnostic.get_by_role('button',name='Скачать пакет',exact=True).click()
        event.value.save_as(str(args.workspace/'diagnostics.zip'))
        with zipfile.ZipFile(args.workspace/'diagnostics.zip') as archive:
            assert set(archive.namelist())=={'manifest.json','versions.json','operations.json','training.json'}
            versions=json.loads(archive.read('versions.json'))
            assert versions['compatible'] and versions['runtimeIdentityAvailable']
            assert versions['serverBuildId']==versions['sourceOnDiskBuildId']==versions['clientBuildId']
            for name in archive.namelist():
                raw=archive.read(name).decode()
                assert str(cache) not in raw and URL not in raw and final_body not in raw
        assert (args.workspace/'diagnostics.zip').stat().st_size<=256_000
        receipt['diagnostics']={'previewAndExplicitDownload':True,'versionsMatchRunningProcess':True,
            'files':['manifest.json','versions.json','operations.json','training.json'],
            'privateFixtureDataExcluded':True,'automaticExternalSend':False}

        code,_=fixture.phone.pairing.issue();page.set_viewport_size({'width':393,'height':852})
        page.goto(fixture.base+'/phone/pair#'+code)
        page.locator('#phoneCards .listing-card').first.wait_for()
        from test_phone_browser import grant_paired_role
        grant_paired_role(fixture,'owner')
        page.goto(fixture.base+'/phone/?card='+identifier)
        page.wait_for_function("document.getElementById('phoneBody').textContent.includes('手动修改保持')")
        assert page.locator('#phoneBody').inner_text()==final_body
        assert page.evaluate('document.documentElement.scrollWidth')<=393
        files=page.locator('#phoneFilesMount')
        files.get_by_role('button',name='HTML',exact=True).first.wait_for()
        assert files.get_by_role('button',name='Intro · PNG',exact=True).first.is_disabled()
        with page.expect_download() as event:files.get_by_role('button',name='HTML',exact=True).first.click()
        event.value.save_as(str(args.workspace/'phone-current.html'))
        assert '手动修改保持' in (args.workspace/'phone-current.html').read_text()
        page.reload();page.wait_for_function("document.getElementById('phoneBody').textContent.includes('手动修改保持')")
        page.screenshot(path=str(args.workspace/'phone-emulation.png'),full_page=True)
        assert not fixture.errors
        receipt['browser']={'backend':'Chromium' if controller.browser_channel=='chromium' else 'Chrome','platform':sys.platform,'desktopReopen':True,'phoneViewport':[393,852],
            'phoneReopen':True,'phoneDownloadCurrentRevision':True,'pageErrors':len(fixture.errors),
            'externalRequestsBlocked':len(blocked),'physicalIPhone':False,'SafariTested':False}
        current_review=photo_instances.load_review(cache,identifier,photo_ids=[item['id'] for item in fixture.assets])
        assert current_review['sha256']==reviewed['sha256']
        assert all(photo_store.asset(cache,item['id'])[2]['id']==item['id'] for item in fixture.assets)
        last=controller.import_diagnostics.status()
        assert initial['state']==last['state']=='idle' and last['reserved']==0
        receipt['preservation']={'manualBody':True,'originalPhotoIdentities':True,'operatorReviewSha256':reviewed['sha256'],
            'diagnosticCampaignUnchanged':True,'isolatedCardCount':2}
    finally:controller.shutdown();desktop_thread.join(5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--production-cache',type=Path,required=True)
    parser.add_argument('--native-ai',action='store_true')
    parser.add_argument('--candidate-adapter',type=Path)
    parser.add_argument('--pdf',action='store_true')
    args=parser.parse_args();args.workspace=args.workspace.resolve();args.production_cache=args.production_cache.expanduser().resolve()
    if args.workspace.exists() or args.workspace.is_relative_to(args.production_cache):parser.error('Use a new workspace outside the production cache')
    args.workspace.mkdir(mode=0o700,parents=True);os.umask(0o077)
    from release_diagnostics import source_identity,_git
    receipt={'version':1,'taskId':'RCN-355','execution':'isolated-real-browser','source':source_identity(ROOT),
        'git':_git(ROOT),'passed':False,'fullCriterionMet':False,
        'limits':['Synthetic photos are not photo accuracy acceptance.','Chrome phone viewport is not physical Safari/iPhone.']}
    before=_inventory(args.production_cache);started=time.monotonic()
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        with _resource(args.production_cache):
            import test_phone_browser as acceptance
            cls=acceptance.PhoneBrowserAcceptance
            cls.setUpClass()
            fixture=cls('runTest')
            try:
                fixture.setUp()
                completed=False
                try:
                    workflow(fixture,args,receipt)
                    completed=True
                finally:
                    # Stop the fixture's own servers before copying SQLite
                    # state. Keep failed attempts concrete and reviewable;
                    # never overwrite a previous workspace or production data.
                    cleanup=fixture.temp.cleanup
                    fixture.temp.cleanup=lambda:None
                    teardown_ok=False
                    try:
                        fixture.tearDown()
                        teardown_ok=True
                    finally:
                        try:
                            if not completed or not teardown_ok:
                                shutil.copytree(fixture.directory,args.workspace/'private-fixture',symlinks=True)
                                (args.workspace/'private-fixture').chmod(0o700)
                                receipt['failureFixture']={'relativePath':'private-fixture','scope':'isolated-synthetic-data','fixtureTeardownCompleted':teardown_ok}
                        finally:cleanup()
            finally:cls.tearDownClass()
        receipt['productionAggregateCountsUnchanged']=before==_inventory(args.production_cache)
        assert receipt['productionAggregateCountsUnchanged']
        receipt['passed']=True
    except BaseException as error:
        receipt['errorType']=type(error).__name__
        raise
    finally:
        receipt['elapsedSeconds']=time.monotonic()-started
        receipt['artifacts']={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in args.workspace.iterdir() if path.is_file() and path.name!='proof.json'}
        atomic(args.workspace/'proof.json',receipt)
    print(json.dumps({'passed':receipt['passed'],'elapsedSeconds':receipt['elapsedSeconds'],'fullCriterionMet':False,'nativeAIExecuted':receipt.get('nativeAI',{}).get('executed',False)}))


if __name__=='__main__':main()
