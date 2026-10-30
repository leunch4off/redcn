"""Read a listing in an ordinary, visible Chrome session. Human checks stay human."""
import re
import threading
import time
from urllib.parse import urljoin

_browser_lock = threading.Lock()

# Read rendered listing blocks first. Remove recommendation cards from the fallback.
SNAPSHOT = r'''() => {
  const body = document.body?.innerText || '';
  const selectors = '[data-name="OfferSummaryInfoItem"], [data-name="ObjectFactoidsItem"], [data-name="OfferFactItem"], [data-name="FeaturesItem"], [data-name="OfferSummaryInfoGroup"], [data-name="OfferTitleNew"], [data-name="OfferTitle"], [data-name="OfferDescription"], [data-name="Description"], [data-name="PriceInfo"], [data-name="GeneralInfoSection"], [data-name="Geo"], [data-name="Underground"], [data-marker="item-view/item-params"], [data-marker="item-view/item-description"], [data-marker="item-view/item-price"], h1';
  const unrelated = '[data-name*="Similar"],[data-name*="Recommend"],[data-marker*="recommend"],[data-marker*="similar"]';
  const fields = [...document.querySelectorAll(selectors)].filter(e=>!e.closest(unrelated));
  const blocks = fields.map(e=>e.innerText?.trim()).filter(Boolean);
  const root = (document.querySelector('main') || document.body).cloneNode(true);
  root.querySelectorAll('script,style,nav,header,footer,[data-name*="Similar"],[data-name*="Recommendation"],[data-marker*="recommend"],[data-marker*="similar"]').forEach(e=>e.remove());
  root.querySelectorAll('p,div,li,section,br,dt,dd').forEach(e=>e.append('\n'));
  // Only the apartment gallery is used: recommendation thumbnails are unrelated
  // apartments. Lazy attributes and srcset often contain the full-size image.
  const gallery = [...document.querySelectorAll('[data-name="OfferGallery"], [data-name="Gallery"], [data-marker*="gallery"]')].filter(e=>!e.closest(unrelated));
  const images = [];
  const add = s => {if(s && !/^(?:data|blob|javascript):/i.test(s)) images.push(s);};
  for(const box of gallery)for(const e of box.querySelectorAll('img,source,a[href]')){
    add(e.getAttribute('data-original'));add(e.getAttribute('data-src'));
    for(const a of ['data-srcset','srcset'])for(const part of (e.getAttribute(a)||'').split(','))add(part.trim().split(/\s+/)[0]);
    if(e.tagName==='A'){
      const href=e.getAttribute('href')||'';if(/\.(?:jpe?g|png|webp)(?:[?#]|$)/i.test(href))add(href);
    }else{add(e.currentSrc);add(e.getAttribute('src'));}
  }
  for(const e of document.querySelectorAll('meta[property="og:image"],meta[name="twitter:image"]'))add(e.content);
  return {title: document.title, body: body.slice(0,12000),
    text: [...new Set(blocks)].join('\n'), fallback: (root.innerText || root.textContent || '').slice(0,40000),
    sourceHtml: fields.map(e=>e.outerHTML).join('\n').slice(0,1500000),
    metadata: [...document.querySelectorAll('script[type="application/ld+json"],script:not([src]),meta[property="og:image"],meta[property="og:description"],meta[property="og:title"]')].filter(e=>e.tagName!=='SCRIPT'||e.type==='application/ld+json'||e.textContent.includes("_cianConfig['frontend-offer-card']")).map(e=>e.outerHTML).join('\n'),
    relatedHtml: [...document.querySelectorAll('[data-name*="Similar"], [data-name*="Recommend"], [data-marker*="similar"], [data-marker*="recommend"]')].map(e=>e.outerHTML).join('\n').slice(0,1500000),
    images: [...new Set(images)].slice(0,120)};
}'''

# A bounded scroll triggers ordinary lazy loading without opening galleries,
# clicking consent/verification controls or running any source-supplied code.
SCROLL = r'''() => {
  const root = document.scrollingElement || document.documentElement;
  const height = Math.max(root.scrollHeight, document.body?.scrollHeight || 0);
  const viewport = Math.max(window.innerHeight || 0, 320);
  const before = root.scrollTop;
  window.scrollTo(0, Math.min(before + Math.floor(viewport * .8), height));
  return {top: root.scrollTop, height, bottom: root.scrollTop + viewport >= height - 4};
}'''
SCROLL_TOP = '() => window.scrollTo(0, 0)'


def usable_snapshot(snapshot):
    """Prefer labelled blocks, with a pruned rendered main-section fallback."""
    ready, challenge, _ = listing_ready(snapshot)
    if not ready and not challenge:
        fallback = {**snapshot, 'text': snapshot.get('fallback', '')}
        if listing_ready(fallback)[0]:
            return fallback
    return snapshot


def capture_scrolled(page, initial, deadline, validate_url, status=None, cancel=None,
                     max_scrolls=24, budget=12):
    """Capture a single owned tab while lazy content appears; stop on a stable bottom.

    The import's original deadline always wins. Images are accumulated because
    galleries may virtualize previous slides; facts use the latest valid snapshot.
    """
    report = status or (lambda message: None)
    snapshot = initial
    images = list(initial.get('images', []))
    end = min(deadline, time.monotonic() + budget)
    scrolls = 0
    bottom_stable = 0
    previous_bottom = None
    report('Прокручиваем объявление: догружаем фотографии и блоки квартиры…')
    try:
        while scrolls < max_scrolls and time.monotonic() < end:
            if cancel and cancel.is_set():
                raise ValueError('Импорт отменён')
            if page.is_closed():
                raise ValueError('Окно Chrome закрыто до завершения импорта')
            validate_url(page.url, resolve=False)
            position = page.evaluate(SCROLL)
            scrolls += 1
            page.wait_for_timeout(min(350, max(1, int((end-time.monotonic())*1000))))
            current = usable_snapshot(page.evaluate(SNAPSHOT))
            ready, challenge, unavailable = listing_ready(current)
            if challenge:
                report('Источник запросил проверку при прокрутке. Пройдите её вручную; Chrome остаётся открыт.')
                # Human verification may appear after a lazy-load request. The
                # original import deadline applies, not the short scroll budget.
                while challenge and time.monotonic() < deadline:
                    if cancel and cancel.is_set():
                        raise ValueError('Импорт отменён')
                    if page.is_closed():
                        raise ValueError('Окно Chrome закрыто до завершения импорта')
                    validate_url(page.url, resolve=False)
                    page.wait_for_timeout(500)
                    current = usable_snapshot(page.evaluate(SNAPSHOT))
                    ready, challenge, unavailable = listing_ready(current)
                if challenge:
                    raise ValueError('Время ручной проверки Chrome истекло. Повторите импорт.')
            if unavailable:
                raise ValueError('Объявление стало недоступно во время чтения.')
            if ready:
                snapshot = current
            images.extend(current.get('images', []))
            signature = (position.get('height'), tuple(dict.fromkeys(images)),
                         current.get('text') or current.get('fallback', ''))
            if position.get('bottom') is True:
                bottom_stable = bottom_stable + 1 if signature == previous_bottom else 0
                previous_bottom = signature
                if bottom_stable >= 2:
                    break
            else:
                bottom_stable = 0
                previous_bottom = None
    finally:
        if not page.is_closed():
            page.evaluate(SCROLL_TOP)
    return {**snapshot, 'images': list(dict.fromkeys(images))[:180]}, {
        'version': 1, 'scrolls': scrolls, 'completed': bottom_stable >= 2,
        'bounded': bottom_stable < 2, 'imageCandidates': len(set(images)),
    }


def listing_ready(snapshot):
    text = snapshot.get('text') or snapshot.get('fallback', '')
    challenge = re.search(r'verify you are human|checking your browser|проверка браузера|доступ ограничен|подтвердите[, ]+что вы|вы не робот|пройдите проверку|проверяем.{0,30}браузер', snapshot.get('body','')[:10000], re.I)
    unavailable = re.search(r'объявление (?:снято|удалено|в архиве)|страница не найдена|объявление больше не актуально', snapshot.get('title','')+'\n'+text[:2500], re.I)
    facts = re.search(r'\d[\d\s\xa0,.]*\s*(?:м²|м2|кв\.?\s*м|㎡)', text, re.I) and re.search(r'\d[\d\s\xa0]*\s*(?:₽|руб)', text, re.I)
    return bool(len(text) >= 80 and facts and not challenge and not unavailable), bool(challenge), bool(unavailable)


def load_browser(url, profile, parse_html, validate_url, timeout=180, status=None, cancel=None, channel='chrome'):
    try:
        from playwright.sync_api import sync_playwright, Error, TimeoutError as BrowserTimeout
    except ImportError as error:
        raise ValueError('Для Chrome установите: python3 -m venv .venv && .venv/bin/python -m pip install -r cli/requirements-browser.txt\nЗатем: .venv/bin/python cli/redcn.py --browser --open') from error
    report = status or (lambda message: None)
    if channel not in ('chrome','chromium'): raise ValueError('Неверный браузер')
    browser_name = 'Google Chrome' if channel=='chrome' else 'Chromium / Chrome for Testing'
    if not _browser_lock.acquire(blocking=False):
        raise ValueError('Другое объявление уже открыто в Chrome. Дождитесь его загрузки или отмените импорт.')
    context = None
    try:
        validate_url(url)
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        report('Открывается '+browser_name+'. Если появится проверка, пройдите её в этом окне. Окно закроется после чтения объявления.')
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(str(profile), channel='chrome' if channel=='chrome' else None, headless=False, accept_downloads=False, timeout=90000)
            except Error as error:
                hint = 'Откройте Google Chrome вручную один раз и закройте окно профиля redcn. Либо установите совместимый браузер: .venv/bin/python -m playwright install chromium и используйте --browser-channel chromium.' if channel=='chrome' else 'Установите браузер: .venv/bin/python -m playwright install chromium. Закройте другое окно профиля redcn.'
                raise ValueError('Не удалось открыть '+browser_name+'. '+hint+' '+str(error).splitlines()[0]) from error
            try:
                if cancel and cancel.is_set(): raise ValueError('Импорт отменён')
                report('Браузер открыт. Загружаем объявление и ждём факты квартиры…')
                # This is a dedicated redcn context, never a CDP attachment to the
                # user's running Chrome. Do not overwrite a restored profile tab.
                page = context.new_page()
                page.set_default_timeout(3000)
                deadline = time.monotonic() + timeout
                try:
                    response = page.goto(url, wait_until='domcontentloaded', timeout=min(45000, timeout*1000))
                    if response and response.status == 404: raise ValueError('Объявление не найдено (404). Проверьте ссылку.')
                except BrowserTimeout:
                    report('Страница ещё загружается. Ожидаем содержимое в открытом Chrome…')
                last, stable, challenge_reported = '', 0, False
                pending_reported = False
                while time.monotonic() < deadline:
                    if cancel and cancel.is_set(): raise ValueError('Импорт отменён')
                    if page.is_closed(): raise ValueError('Окно Chrome закрыто до завершения импорта')
                    current_url = page.url
                    if current_url == 'about:blank':
                        if not pending_reported:
                            report('Chrome ещё не установил соединение с источником. Ссылка корректна; ожидаем загрузку страницы…')
                            pending_reported = True
                        page.wait_for_timeout(1000)
                        continue
                    if current_url.startswith('chrome-error:'):
                        raise ValueError('Chrome не смог подключиться к источнику. Проверьте доступность ссылки в обычном браузере либо используйте --input. Настройки сертификатов не изменяются.')
                    validate_url(current_url, resolve=False)
                    snapshot = page.evaluate(SNAPSHOT)
                    # A selector can be renamed by a source; use the pruned rendered
                    # main section when it contains facts absent from the small blocks.
                    snapshot = usable_snapshot(snapshot)
                    ready, challenge, unavailable = listing_ready(snapshot)
                    if unavailable: raise ValueError('Объявление снято или недоступно. Вставьте сохранённый текст вручную.')
                    if challenge and not challenge_reported:
                        report('Нужна ваша проверка в окне Chrome. Пройдите её вручную; программа ждёт, ничего не нажимая.')
                        challenge_reported = True
                    current = snapshot['text'] or snapshot['fallback']
                    stable = stable + 1 if ready and current == last else 0
                    last = current
                    if ready and stable >= 2:
                        from html import escape
                        snapshot, capture = capture_scrolled(page, snapshot, deadline, validate_url,
                                                             status=report, cancel=cancel)
                        current = snapshot['text'] or snapshot.get('fallback', '')
                        result = parse_html(snapshot.get('metadata','')+snapshot.get('sourceHtml','')+'<p>'+escape(current)+'</p>'+snapshot.get('relatedHtml',''), url)
                        # Visible summary includes price, floor and transit absent from JSON-LD.
                        result['text'] = current[:40000]
                        for image in snapshot['images']:
                            try:
                                image = validate_url(urljoin(page.url, image), images=True, resolve=False)
                                if image not in result['images']: result['images'].append(image)
                            except ValueError: pass
                        result['images'] = result['images'][:30]
                        result['loader'] = 'chrome'
                        result['browser_channel'] = channel
                        result['capture'] = capture
                        if capture['bounded']:
                            report('Лимит прокрутки достигнут: сохранены доступные данные. Проверьте полноту фотографий в карточке.')
                        report('Карточка прочитана. Закрываем окно импорта Chrome.')
                        return result
                    page.wait_for_timeout(500)
                raise ValueError('Время ожидания Chrome истекло. Проверка или загрузка не завершилась. Повторите --browser --browser-timeout 300 либо вставьте текст вручную.')
            finally:
                context.close()
    except Error as error:
        raise ValueError('Chrome не прочитал страницу: '+str(error).splitlines()[0]) from error
    finally:
        _browser_lock.release()
