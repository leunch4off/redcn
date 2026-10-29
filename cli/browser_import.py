"""Read a listing in an ordinary, visible Chrome session. Human checks stay human."""
import re
import threading
import time

_browser_lock = threading.Lock()

# Read rendered listing blocks first. Remove recommendation cards from the fallback.
SNAPSHOT = r'''() => {
  const body = document.body?.innerText || '';
  const selectors = '[data-name="OfferSummaryInfoItem"], [data-name="ObjectFactoidsItem"], [data-name="OfferFactItem"], [data-name="FeaturesItem"], [data-name="OfferSummaryInfoGroup"], [data-name="OfferTitleNew"], [data-name="OfferTitle"], [data-name="OfferDescription"], [data-name="Description"], [data-name="PriceInfo"], [data-name="GeneralInfoSection"], [data-name="Geo"], [data-name="Underground"], [data-marker="item-view/item-params"], [data-marker="item-view/item-description"], [data-marker="item-view/item-price"], h1';
  const blocks = [...document.querySelectorAll(selectors)].map(e=>e.innerText?.trim()).filter(Boolean);
  const root = (document.querySelector('main') || document.body).cloneNode(true);
  root.querySelectorAll('script,style,nav,header,footer,[data-name*="Similar"],[data-name*="Recommendation"],[data-marker*="recommend"],[data-marker*="similar"]').forEach(e=>e.remove());
  root.querySelectorAll('p,div,li,section,br,dt,dd').forEach(e=>e.append('\n'));
  return {title: document.title, body: body.slice(0,12000),
    text: [...new Set(blocks)].join('\n'), fallback: (root.innerText || root.textContent || '').slice(0,40000),
    metadata: [...document.querySelectorAll('script[type="application/ld+json"],script:not([src]),meta[property="og:image"],meta[property="og:description"],meta[property="og:title"]')].filter(e=>e.tagName!=='SCRIPT'||e.type==='application/ld+json'||e.textContent.includes("_cianConfig['frontend-offer-card']")).map(e=>e.outerHTML).join('\n'),
    relatedHtml: [...document.querySelectorAll('[data-name*="Similar"], [data-name*="Recommend"], [data-marker*="similar"], [data-marker*="recommend"]')].map(e=>e.outerHTML).join('\n').slice(0,1500000),
    images: [...document.querySelectorAll('[data-name="OfferGallery"] img,[data-name="Gallery"] img,[data-marker*="gallery"] img')].map(e=>e.currentSrc || e.src).filter(Boolean).slice(0,60)};
}'''


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
                page = context.pages[0] if context.pages else context.new_page()
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
                    if not listing_ready(snapshot)[0] and not listing_ready(snapshot)[1]:
                        fallback = {**snapshot, 'text':snapshot['fallback']}
                        if listing_ready(fallback)[0]: snapshot = fallback
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
                        result = parse_html(snapshot['metadata']+'<p>'+escape(current)+'</p>'+snapshot.get('relatedHtml',''), url)
                        # Visible summary includes price, floor and transit absent from JSON-LD.
                        result['text'] = current[:40000]
                        for image in snapshot['images']:
                            try:
                                image = validate_url(image, images=True, resolve=False)
                                if image not in result['images']: result['images'].append(image)
                            except ValueError: pass
                        result['images'] = result['images'][:30]
                        result['loader'] = 'chrome'
                        result['browser_channel'] = channel
                        report('Карточка прочитана. Закрываем окно импорта Chrome.')
                        return result
                    page.wait_for_timeout(1000)
                raise ValueError('Время ожидания Chrome истекло. Проверка или загрузка не завершилась. Повторите --browser --browser-timeout 300 либо вставьте текст вручную.')
            finally:
                context.close()
    except Error as error:
        raise ValueError('Chrome не прочитал страницу: '+str(error).splitlines()[0]) from error
    finally:
        _browser_lock.release()
