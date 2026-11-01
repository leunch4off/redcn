#!/usr/bin/env python3
"""Unprofiled readiness timing against the disposable two-photo fixture."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import threading
import time

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests'))
sys.path.insert(0,str(root/'cli'))
import test_presentation_browser as presentation


def measure(mode):
    cls=presentation.PresentationAcceptance
    cls.setUpClass()
    fixture=cls('test_new_card_first_export_keeps_stable_id_in_draft')
    fixture.setUp()
    thread=threading.Thread(target=fixture.controller.serve_forever,daemon=True)
    thread.start()
    page=fixture.page
    page.set_default_timeout(30000)
    record={'sourceHead':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),'mode':mode,'fixture':'PresentationAcceptance: two synthetic 640x420 PNG photos','profiler':False,'expectedPreparedPhotos':2,'fixturePhotoCount':len(fixture.assets),'operationTimeoutMs':30000,'errors':[],'pageErrors':fixture.errors,'clickElapsedMs':None,'canvasUsableElapsedMs':None,'photosReadyElapsedMs':None}
    started=None
    try:
        if len(fixture.assets)!=2:raise AssertionError('Readiness fixture must contain exactly two attached photos')
        page.add_init_script("if(window===window.top){localStorage.setItem('most-photo-analyze','false');localStorage.setItem('most-photo-autoload','false')}")
        # Neither timing mode depends on a public map/tile provider.
        for pattern in ('**/api/local/listing-map','**/api/phone/listing-map'):
            page.route(pattern,lambda route:route.fulfill(status=400,json={'error':'Fixture map deliberately unavailable'}))
        if mode=='desktop':
            page.set_viewport_size({'width':1440,'height':1100})
            base='http://127.0.0.1:'+str(fixture.controller.server_port)
            page.goto(base+'/listing/'+fixture.card_id,wait_until='domcontentloaded')
            page.wait_for_function("document.documentElement.dataset.editorReady==='true'&&document.getElementById('output').value.includes('两间独立')",timeout=30000)
            selector='#openPresentation'
            mount_id='presentationMount'
        else:
            presentation.acceptance.PhoneBrowserAcceptance.connect(fixture)
            page.locator('#phoneCards .listing-card').first.click(timeout=30000)
            page.locator('#phoneDetailTitle').wait_for(state='visible',timeout=30000)
            selector='#phoneTabs [data-phone-view="design"]'
            mount_id='phoneDesignMount'
        # Production readiness marks are read only after the monotonic timings;
        # there is no CPU profiler, screenshot, export or polling instrumentation.
        page.evaluate("selector=>{performance.clearMarks('redcn-readiness-click');document.querySelector(selector).addEventListener('click',()=>performance.mark('redcn-readiness-click'),{capture:true,once:true});}",selector)
        started=time.monotonic()
        try:
            page.locator(selector).click(timeout=30000)
            record['clickElapsedMs']=round((time.monotonic()-started)*1000,1)
        except Exception as error:
            record['errors'].append({'stage':'click','elapsedMs':round((time.monotonic()-started)*1000,1),'message':str(error)})
        try:
            page.locator('#'+mount_id+' #designCanvasEditor').wait_for(state='visible',timeout=30000)
            page.wait_for_function("id=>{const host=document.querySelector('#'+id+' #designCanvasEditor');if(!host||host.dataset.previewReady!=='true')return false;const bounds=host.getBoundingClientRect();return bounds.width>0&&bounds.height>0;}",arg=mount_id,timeout=30000)
            record['canvasUsableElapsedMs']=round((time.monotonic()-started)*1000,1)
        except Exception as error:
            record['errors'].append({'stage':'canvas-ready','elapsedMs':round((time.monotonic()-started)*1000,1),'message':str(error)})
        try:
            page.wait_for_function("id=>{const mount=document.getElementById(id),status=mount?.querySelector('#presentationStatus')?.textContent||'',match=status.match(/(?:^|[^\\d])(\\d+)\\s+фото(?:[.\\s]|$)/u);return mount?.dataset.photosReady==='true'&&Number(match?.[1])===2;}",arg=mount_id,timeout=30000)
            record['photosReadyElapsedMs']=round((time.monotonic()-started)*1000,1)
        except Exception as error:
            record['errors'].append({'stage':'photos-ready','elapsedMs':round((time.monotonic()-started)*1000,1),'message':str(error)})
        record['observed']=page.evaluate("id=>{const start=performance.getEntriesByName('redcn-readiness-click').at(-1)?.startTime;const relative=name=>performance.getEntriesByName(name).filter(entry=>start!==undefined&&entry.startTime>=start).map(entry=>Math.round((entry.startTime-start)*10)/10);const status=document.getElementById(id)?.querySelector('#presentationStatus')?.textContent||'',count=status.match(/(?:^|[^\\d])(\\d+)\\s+фото(?:[.\\s]|$)/u);return {preparedPhotoCount:count?Number(count[1]):null,photosStage:document.getElementById(id)?.dataset.photosStage||'',photosReady:document.getElementById(id)?.dataset.photosReady||'',canvasReady:document.querySelector('#'+id+' #designCanvasEditor')?.dataset.previewReady||'',status,toast:document.getElementById('phoneToast')?.textContent||'',internalCanvasMarkMs:relative('redcn-designer-canvas-ready'),internalPhotosMarkMs:relative('redcn-designer-photos-ready')};}",mount_id)
        # The photos predicate can already be true at the canvas observation.
        # Internal marks report its actual production stage; the monotonic
        # elapsed above is when that predicate was observable from the driver.
        record['photosObservation']='Requires photosReady=true AND the completed studio status reporting exactly 2 photos; observed after usable-canvas wait, with production photo mark reported separately if earlier.'
    except Exception as error:
        record['errors'].append({'stage':'fixture-or-observation','message':str(error)})
    finally:
        record['canvasUnder10Seconds']=record['canvasUsableElapsedMs'] is not None and record['canvasUsableElapsedMs']<10000
        record['photosObservedUnder10Seconds']=record['photosReadyElapsedMs'] is not None and record['photosReadyElapsedMs']<10000
        record['twoPreparedPhotosValidated']=record['photosReadyElapsedMs'] is not None and record.get('observed',{}).get('preparedPhotoCount')==2 and record['fixturePhotoCount']==2
        record['pageErrors']=list(fixture.errors)
        print(json.dumps(record,ensure_ascii=False),flush=True)
        fixture.controller.shutdown();thread.join(timeout=5)
        try:fixture.tearDown()
        finally:cls.tearDownClass()
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['desktop','phone','both'],default='both');args=parser.parse_args()
    for mode in (['desktop','phone'] if args.mode=='both' else [args.mode]):measure(mode)
