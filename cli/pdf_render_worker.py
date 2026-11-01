"""Owned static PDF worker. HTML enters only stdin; stdout contains only PDF."""
import json
import sys

from card_assets import MAX_ATTACHMENT, MAX_HTML, validate_html, _PublicationText
from client_publication import assert_public_text


class _WorkerFailure(Exception):
    pass


def validate_document(html):
    validate_html(html)
    parser = _PublicationText()
    parser.feed(html)
    parser.close()
    assert_public_text(''.join(parser.text))
    assert_public_text(' '.join(parser.text))
    for value in parser.metadata:
        assert_public_text(value)
    # create_export already checks service claims against the private card and
    # receipts. Those private records never cross this rendering-only boundary.


def render(html, browser_channel):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise _WorkerFailure('dependency') from None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome' if browser_channel == 'chrome' else None,
                                             headless=True, timeout=45000)
        try:
            context = browser.new_context(java_script_enabled=False, offline=True, service_workers='block')
            try:
                context.route('**/*', lambda route: route.abort())
                page = context.new_page()
                page.set_content(html, wait_until='load', timeout=30000)
                # Fixed trusted expression; authored JavaScript stays disabled.
                page.evaluate('async () => { let timer; try { await Promise.race([(async()=>{ await document.fonts.ready; await Promise.all(Array.from(document.images, image => image.decode())); })(), new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error("Image preparation timeout")),15000);})]); } finally { clearTimeout(timer); } }')
                content = page.pdf(format='A4', print_background=True, prefer_css_page_size=True)
            finally:
                context.close()
        finally:
            browser.close()
    if not content.startswith(b'%PDF-'):
        raise _WorkerFailure('invalid_pdf')
    if len(content) > MAX_ATTACHMENT:
        raise _WorkerFailure('too_large')
    return content


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('chrome', 'chromium'):
        raise _WorkerFailure('validation')
    raw = sys.stdin.buffer.read(MAX_HTML + 1)
    if len(raw) > MAX_HTML:
        raise _WorkerFailure('validation')
    try:
        html = raw.decode('utf-8')
        validate_document(html)
    except (ValueError, UnicodeError):
        raise _WorkerFailure('validation') from None
    content = render(html, sys.argv[1])
    # Do not emit success bytes until Chrome, context and driver cleanup finish.
    sys.stdout.buffer.write(content)
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Browser exceptions may quote page content. Return only an allowlisted
        # code, never a traceback, source text, cache path or driver diagnostics.
        code = str(error) if isinstance(error, _WorkerFailure) else 'render'
        if code not in ('dependency', 'validation', 'too_large', 'invalid_pdf'):
            code = 'render'
        sys.stderr.write(json.dumps({'error': code}))
        sys.stderr.flush()
        raise SystemExit(2)
