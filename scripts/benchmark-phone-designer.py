#!/usr/bin/env python3
"""External public-shell measurement with entirely synthetic private API data.

No real pairing, card, profile, photograph or import endpoint is contacted.
This measures external HTML/module delivery and client work, not a complete
authenticated-user performance acceptance. Run with the Mac's Playwright env.
The optional --skip-tuna-warning excludes TUNA's documented provider notice;
it does not measure the normal first-visitor notice or authentication flow.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import struct
import time
from urllib.parse import urlsplit, urlunsplit
import zlib

CARD_ID = 'e'*32
USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
CAPABILITIES = {'readCards': True, 'editCards': True, 'importListings': True,
    'readDesign': True, 'manageDesign': True, 'proposeDictionary': True,
    'manageDictionary': True, 'readDossier': False, 'manageDossier': False,
    'manageSettings': False, 'manageDiagnostics': False, 'readDiagnostics': False,
    'downloadDiagnostics': False}


def phone_url(value):
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ('', '/', '/phone', '/phone/')):
        raise ValueError('Use the public HTTPS origin or /phone/ URL without credentials, query or fragment')
    return urlunsplit((parts.scheme, parts.netloc, '/phone/', '', ''))


def provider_headers(value, skip_tuna_warning=False):
    """Optional public-provider notice option, restricted to TUNA HTTPS hosts.

    https://tuna.am/docs/tunnels/http/ documents this header. It is applied only
    to allowlisted public requests at the validated origin, never globally to
    the browser context, redirects, private APIs or another destination.
    """
    parts = urlsplit(phone_url(value))
    if not skip_tuna_warning:
        return {}
    if (not any(parts.hostname.endswith(suffix) for suffix in ('.tuna.am', '.tuna.free'))
            or parts.port not in (None, 443)):
        raise ValueError('--skip-tuna-warning requires a public TUNA HTTPS subdomain on port 443')
    return {'tuna-skip-browser-warning': '1'}


def fixture_png(number, width=640, height=420):
    """Deterministic generated pixels; no source images or imaging packages."""
    def chunk(kind, value):
        return struct.pack('!I', len(value))+kind+value+struct.pack('!I', zlib.crc32(kind+value)&0xffffffff)
    color = ((43+number*23)%220, (92+number*31)%220, (118+number*13)%220)
    raw = b''.join(b'\0'+bytes(color)*width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))+chunk(b'IDAT', zlib.compress(raw))+chunk(b'IEND', b'')


def fixtures(photo_count):
    images, media = {}, []
    for index in range(photo_count):
        content = fixture_png(index)
        digest = hashlib.sha256(content).hexdigest()
        images[digest] = content
        media.append({'id': digest, 'url': '/api/phone/media/'+digest,
            'sourceUrl': 'https://fixture.invalid/photo-'+str(index+1)+'.png',
            'mime': 'image/png', 'width': 640, 'height': 420, 'bytes': len(content), 'kind': 'original'})
    state = {'paired': True, 'csrfToken': 'synthetic-benchmark-only', 'role': 'editor',
        'capabilities': CAPABILITIES, 'release': 'synthetic-api-benchmark', 'activeJob': None}
    card = {'id': CARD_ID, 'updated': 1_700_000_000, 'titles': ['设计性能测试 · 合成房源'],
        'title': '设计性能测试 · 合成房源', 'body': '这是一套合成测试卡片，不是真实房源。',
        'tags': '#设计测试', 'fields': {'price': 80000, 'area': 54, 'metro': '普罗克希诺',
            'rooms': 2, 'floor': 5, 'floors': 17, 'editorIdentity': 'synthetic-performance-fixture',
            'photos': [row['sourceUrl'] for row in media], 'features': []},
        'media': media, 'mediaStatus': 'ready', 'expectedPhotoCount': photo_count,
        'photoCount': photo_count, 'cachedPhotoCount': photo_count,
        'photoProcessing': {'total': photo_count, 'downloaded': photo_count,
            'downloadFailed': 0, 'failed': 0, 'skipped': photo_count, 'cropped': 0}}
    summary = {'id': CARD_ID, 'title': card['title'], 'updated': card['updated'],
        'price': 80000, 'metro': '普罗克希诺', 'photoCount': photo_count,
        'cachedPhotoCount': photo_count, 'mediaStatus': 'ready',
        'coverUrl': '/api/phone/previews/'+next(iter(images))}
    return state, card, summary, images


def fixture_response(method, path, data):
    """Every API request is answered locally, including unsupported requests."""
    state, card, summary, images = data
    if method != 'GET':
        return 403, 'application/json', {'error': 'Synthetic benchmark forbids all API writes'}
    if path == '/api/phone/state':
        value = state
    elif path == '/api/phone/cards':
        value = {**state, 'cards': [summary], 'hasMore': False, 'cursor': '1700000000', 'serverTime': 1_700_000_000}
    elif path == '/api/phone/cards/'+CARD_ID:
        value = card
    elif path == '/api/phone/design':
        value = {'template': None, 'updated': None, 'rules': {}}
    elif path == '/api/phone/card-exports/'+CARD_ID:
        value = {'exports': []}
    elif path.startswith(('/api/phone/media/', '/api/phone/previews/')):
        content = images.get(path.rsplit('/', 1)[-1])
        if content is not None:
            return 200, 'image/png', content
        return 404, 'application/json', {'error': 'Unknown synthetic image'}
    else:
        return 404, 'application/json', {'error': 'Not provided by the synthetic benchmark'}
    return 200, 'application/json', value


async def run_once(playwright, url, number, args):
    public_headers = provider_headers(url, args.skip_tuna_warning)
    browser = await playwright.chromium.launch(channel=args.channel, headless=True)
    context = await browser.new_context(viewport={'width': 393, 'height': 852},
        device_scale_factor=3, is_mobile=True, has_touch=True, user_agent=USER_AGENT,
        service_workers='block')
    data = fixtures(args.photos)
    allowed_origin = urlsplit(url).netloc
    api_calls, blocked, errors = [], [], []
    public_paths = {'/phone/', '/phone/designer.mjs', '/phone/routes.mjs', '/phone/phone.css',
        '/phone/phone.mjs', '/phone/phone-bootstrap.js', '/phone/sw.js', '/phone/manifest.webmanifest',
        '/phone.webmanifest', '/phone/icon-192.png', '/phone/icon-512.png',
        '/phone/apple-touch-icon.png', '/favicon.ico'}
    async def intercept(route):
        request = route.request
        parts = urlsplit(request.url)
        if parts.path.startswith('/api/'):
            api_calls.append({'method': request.method, 'path': parts.path})
            status, mime, value = fixture_response(request.method, parts.path, data)
            if args.fixture_api_delay_ms:
                await asyncio.sleep(args.fixture_api_delay_ms/1000)
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            await route.fulfill(status=status, content_type=mime, body=body)
            return
        if (request.method in ('GET', 'HEAD') and parts.scheme == 'https' and parts.netloc == allowed_origin
                and (parts.path in public_paths or parts.path.startswith(('/phone/icons/', '/icons/')))):
            if public_headers:
                await route.continue_(headers={**request.headers, **public_headers})
            else:
                await route.continue_()
        else:
            blocked.append({'method': request.method, 'path': parts.path})
            await route.abort('blockedbyclient')
    # Routing disables Chromium HTTP cache. A fresh browser/context per run also
    # excludes cookies, localStorage, HTTP cache and a previous compiled module.
    await context.route('**/*', intercept)
    page = await context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)[:500]))
    page.set_default_timeout(args.timeout_seconds*1000)
    result = {'run': number, 'status': 'error', 'providerWarningExcluded': bool(public_headers)}
    started = time.monotonic()
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=args.timeout_seconds*1000)
        await page.locator('#phoneCards .listing-card').first.wait_for(state='visible')
        result['navigationToCardsMs'] = round((time.monotonic()-started)*1000, 1)
        await page.locator('#phoneCards .listing-card').first.click()
        await page.locator('#phoneDetail').wait_for(state='visible')
        # Open immediately: there is no inserted reading pause to make prewarm
        # look faster. The natural click/render lead is included in the report.
        await page.evaluate("window.__designerBenchmarkOpen=performance.now()")
        await page.locator('#phoneTabs [data-phone-view="design"]').click()
        await page.wait_for_function("document.getElementById('phoneDesignMount').dataset.editorReady==='true'")
        result['openToEditorMs'] = round(await page.evaluate("performance.now()-window.__designerBenchmarkOpen"), 1)
        await page.wait_for_function("document.getElementById('phoneDesignMount').dataset.photosReady==='true'")
        result['openToPhotosMs'] = round(await page.evaluate("performance.now()-window.__designerBenchmarkOpen"), 1)
        result.update(await page.evaluate('''() => {
            const mount=document.getElementById('phoneDesignMount');
            const module=performance.getEntriesByType('resource').find(row=>new URL(row.name).pathname==='/phone/designer.mjs');
            const first=name=>performance.getEntriesByName(name)[0]?.startTime??null;
            return {module:module?{startMs:module.startTime,durationMs:module.duration,
                transferBytes:module.transferSize,encodedBytes:module.encodedBodySize,decodedBytes:module.decodedBodySize}:null,
                moduleWarmLeadMs:module?Math.max(0,window.__designerBenchmarkOpen-module.startTime):null,
                marks:{editor:first('redcn-designer-editor-ready'),photos:first('redcn-designer-photos-ready')},
                mount:{editorReady:mount.dataset.editorReady,photosReady:mount.dataset.photosReady,
                    photosStage:mount.dataset.photosStage,photosFetchMs:Number(mount.dataset.photosFetchMs)||null,
                    moduleWaitMs:Number(mount.dataset.moduleWaitMs)||0,setupSyncMs:Number(mount.dataset.setupSyncMs)||0,
                    templateReadMs:Number(mount.dataset.templateReadMs)||0,canvasWaitMs:Number(mount.dataset.canvasWaitMs)||0,
                    controlsBuildMs:Number(mount.dataset.controlsBuildMs)||0,canvasSetupMs:Number(mount.dataset.canvasSetupMs)||0,
                    canvasPreviewReady:document.getElementById('designCanvasEditor')?.dataset.previewReady,
                    canvasDrawMs:Number(document.getElementById('designCanvasEditor')?.dataset.drawMs)||0},
                viewport:{width:innerWidth,height:innerHeight,visualWidth:visualViewport?.width,
                    visualHeight:visualViewport?.height,documentWidth:document.documentElement.scrollWidth}};
        }'''))
        result['status'] = 'measured'
        result['syntheticEditorUnder10Seconds'] = result['openToEditorMs'] < 10000
    except Exception as error:
        result['error'] = str(error).splitlines()[0][:700]
        result['elapsedMs'] = round((time.monotonic()-started)*1000, 1)
    finally:
        result.update(pageErrors=errors, blockedRequests=blocked,
            syntheticApiRequests=api_calls, actualPrivateApiRequests=0,
            apiWritesBlocked=sum(row['method'] != 'GET' for row in api_calls))
        await context.close()
        await browser.close()
    return result


async def main_async(args):
    from playwright.async_api import async_playwright
    url = phone_url(args.url)
    runs = []
    async with async_playwright() as runtime:
        for number in range(1, args.runs+1):
            run = await run_once(runtime, url, number, args)
            runs.append(run)
            print(json.dumps({'run': number, **{key: run[key] for key in ('status', 'openToEditorMs', 'openToPhotosMs', 'error') if key in run}}, ensure_ascii=False), flush=True)
    measured = [run['openToEditorMs'] for run in runs if run['status'] == 'measured']
    report = {'version': 1, 'measuredAt': datetime.now(timezone.utc).isoformat(),
        'publicUrl': url, 'method': 'external-public-shell-with-synthetic-private-api',
        'browser': 'Chrome/Chromium, iPhone-sized emulation; not physical iPhone Safari',
        'viewport': {'width': 393, 'height': 852}, 'coldBrowserPerRun': True,
        'syntheticPhotos': args.photos, 'syntheticPhotoSize': [640, 420],
        'fixtureApiDelayMs': args.fixture_api_delay_ms,
        'providerWarningExcluded': args.skip_tuna_warning,
        'realUserSessionAcceptance': False,
        'limits': ['Real external HTTPS HTML and module transfer are measured.',
            'Authentication, CSRF, feed, shared template and generated image pixels are synthetic.',
            'All API requests are intercepted locally; no real pairing, private read, write or import is performed.',
            ('TUNA first-visitor warning is excluded using its documented public-provider header; '
                'this does not measure the first-visitor warning or authentication experience.'
                if args.skip_tuna_warning else 'Provider first-visitor warning uses default browser behavior and is not excluded.'),
            'Synthetic-photo readiness does not measure real gallery network delivery or difficult image content.',
            'A result below 10 seconds is not complete RCN-287 live-user acceptance.'],
        'summary': {'completedRuns': len(measured), 'requestedRuns': args.runs,
            'editorMedianMs': round(statistics.median(measured), 1) if measured else None,
            'editorMaxMs': max(measured) if measured else None,
            'allSyntheticEditorRunsUnder10Seconds': len(measured) == args.runs and max(measured) < 10000 if measured else False},
        'runs': runs}
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'summary': report['summary'], 'providerWarningExcluded': args.skip_tuna_warning,
        'realUserSessionAcceptance': False}, ensure_ascii=False), flush=True)
    return 0 if len(measured) == args.runs else 1


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--runs', type=int, default=3, choices=range(1, 6))
    parser.add_argument('--photos', type=int, default=3, choices=range(1, 31))
    parser.add_argument('--timeout-seconds', type=int, default=45, choices=range(10, 91))
    parser.add_argument('--fixture-api-delay-ms', type=int, default=0, choices=range(0, 1001))
    parser.add_argument('--channel', choices=('chrome', 'chromium'), default='chrome')
    parser.add_argument('--output')
    parser.add_argument('--skip-tuna-warning', action='store_true', default=False,
        help='Exclude TUNA provider notice with its documented header; TUNA HTTPS subdomains only')
    result = parser.parse_args(argv)
    phone_url(result.url)
    provider_headers(result.url, result.skip_tuna_warning)
    return result


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main_async(arguments())))
