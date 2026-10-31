"""Read a listing in an ordinary, visible Chrome session. Human checks stay human."""
import re
import threading
import time
from urllib.parse import urljoin
from job_progress import emit

_browser_lock = threading.Lock()

from browser_dom import source_scripts
from source_adapters import adapter_for

SNAPSHOT, SECTION_PROBE = source_scripts(adapter_for('https://www.cian.ru/'))

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

# Inspect only known listing blocks. No gallery clicks, consent clicks or
# recommendation cards are involved. Scrolling to the block triggers ordinary
# IntersectionObserver/image lazy loading; readiness is checked immediately.
SECTION_FRAME = '''() => new Promise(resolve => {
  let done=false;
  const finish=()=>{if(!done){done=true;resolve();}};
  const timer=setTimeout(finish,80);
  requestAnimationFrame(()=>{clearTimeout(timer);finish();});
})'''


def capture_sections(page, initial, deadline, validate_url, status=None, cancel=None,
                     budget=6, section_wait=1.5, poll_ms=80,
                     snapshot_script=SNAPSHOT, section_probe=SECTION_PROBE):
    """Visit gallery → description → characteristics without a fixed ready wait.

    A section is ready as soon as its actual content is present. A decoded image
    proves only the visible gallery, not that every photograph has been found.
    Unknown layouts and incomplete blocks remain eligible for the bounded
    page-scroll fallback in load_browser.
    """
    snapshot = initial
    images = list(initial.get('images', []))
    started = time.monotonic()
    end = min(deadline, started + budget)
    sections = []
    manual_seconds = 0
    labels = {'gallery':'фотографии', 'description':'описание квартиры',
              'characteristics':'характеристики, мебель и техника'}
    try:
        for section in ('gallery', 'description', 'characteristics'):
            section_started = time.monotonic()
            phase_end = min(end, section_started + section_wait)
            moved = False
            target_index = 0
            visited_blocks = set()
            reads = 0
            state = {'found':False, 'ready':False}
            emit(status, 'Переходим к блоку '+str(len(sections)+1)+' из 3: '+labels[section]+'. Уже загруженные данные читаем сразу.',
                 stage='scroll', section=section, maxTotalSeconds=budget)
            while time.monotonic() < phase_end:
                if cancel and cancel.is_set():
                    raise ValueError('Импорт отменён')
                if page.is_closed():
                    raise ValueError('Окно Chrome закрыто до завершения импорта')
                validate_url(page.url, resolve=False)
                state = page.evaluate(section_probe, {'section':section, 'scroll':not moved, 'index':target_index})
                reads += 1
                if state.get('found') and not moved:
                    moved = True
                    visited_blocks.add(target_index)
                    if not state.get('targetReady', state.get('ready')):
                        # Only a missing block needs a frame for scroll observers.
                        # The 80ms fallback also works in background/minimized tabs
                        # where animation frames may be suspended by Chrome.
                        page.evaluate(SECTION_FRAME)
                        state = page.evaluate(section_probe, {'section':section, 'index':target_index})
                current = usable_snapshot(page.evaluate(snapshot_script))
                ready, challenge, unavailable = listing_ready(current)
                if challenge:
                    challenged_at = time.monotonic()
                    emit(status, 'Источник запросил проверку. Пройдите её вручную в Chrome; чтение блоков приостановлено.',
                         stage='challenge', awaitingUser=True)
                    while challenge and time.monotonic() < deadline:
                        if cancel and cancel.is_set():
                            raise ValueError('Импорт отменён')
                        if page.is_closed():
                            raise ValueError('Окно Chrome закрыто до завершения импорта')
                        validate_url(page.url, resolve=False)
                        page.wait_for_timeout(min(500, max(1, int((deadline-time.monotonic())*1000))))
                        current = usable_snapshot(page.evaluate(snapshot_script))
                        ready, challenge, unavailable = listing_ready(current)
                    if challenge:
                        raise ValueError('Время ручной проверки Chrome истекло. Повторите импорт.')
                    delay = time.monotonic()-challenged_at
                    manual_seconds += delay
                    end = min(deadline, end+delay)
                    phase_end = min(end, phase_end+delay)
                    moved = False
                    emit(status, 'Проверка завершена. Продолжаем чтение блока: '+labels[section]+'.',
                         stage='scroll', section=section, awaitingUser=False)
                    continue
                if unavailable:
                    raise ValueError('Объявление стало недоступно во время чтения.')
                if ready:
                    snapshot = current
                images.extend(current.get('images', []))
                if state.get('ready'):
                    break
                if state.get('targetReady') and isinstance(state.get('nextPendingIndex'), int) and state['nextPendingIndex']>=0:
                    target_index = state['nextPendingIndex']
                    moved = False
                    continue
                remaining = phase_end-time.monotonic()
                if remaining > 0:
                    page.wait_for_timeout(min(poll_ms, max(1, int(remaining*1000))))
            result = {'section':section, **state, 'reads':reads,
                      'blocksVisited':len(visited_blocks),
                      'seconds':round(time.monotonic()-section_started, 3)}
            sections.append(result)
            emit(status, ('Блок готов: ' if state.get('ready') else 'Блок пока неполный: ')+labels[section]+
                 '. Найдено кандидатов фото: '+str(len(set(images)))+'.',
                 stage='scroll', section=section, completed=len(sections),
                 sectionReady=bool(state.get('ready')))
    finally:
        if not page.is_closed():
            page.evaluate(SCROLL_TOP)
    complete = len(sections)==3 and all(row.get('ready') for row in sections)
    return {**snapshot, 'images':list(dict.fromkeys(images))[:180]}, {
        'version':2, 'strategy':'sections', 'sections':sections,
        'completed':complete, 'bounded':not complete, 'pageBottomReached':False,
        'scrolls':sum(row['blocksVisited'] for row in sections),
        'imageCandidates':len(set(images)), 'seconds':round(time.monotonic()-started, 3),
        'activeSeconds':round(max(0, time.monotonic()-started-manual_seconds), 3),
    }


def usable_snapshot(snapshot):
    """Prefer labelled blocks, with a pruned rendered main-section fallback."""
    ready, challenge, _ = listing_ready(snapshot)
    if not ready and not challenge:
        fallback = {**snapshot, 'text': snapshot.get('fallback', '')}
        if listing_ready(fallback)[0]:
            return fallback
    return snapshot


def capture_scrolled(page, initial, deadline, validate_url, status=None, cancel=None,
                     max_scrolls=24, budget=12, stage_budget=None, snapshot_script=SNAPSHOT):
    """Capture a single owned tab while lazy content appears; stop on a stable bottom.

    The import's original deadline always wins. Images are accumulated because
    galleries may virtualize previous slides; facts use the latest valid snapshot.
    """
    report = status
    snapshot = initial
    images = list(initial.get('images', []))
    end = min(deadline, time.monotonic() + budget)
    scrolls = 0
    bottom_stable = 0
    previous_bottom = None
    emit(report,'Прокручиваем объявление: догружаем фотографии и блоки квартиры…',stage='scroll',
         maxTotalSeconds=stage_budget or budget, completed=0)
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
            current = usable_snapshot(page.evaluate(snapshot_script))
            ready, challenge, unavailable = listing_ready(current)
            if challenge:
                emit(report,'Источник запросил проверку при прокрутке. Пройдите её вручную; Chrome остаётся открыт.',stage='challenge',awaitingUser=True)
                # Human verification may appear after a lazy-load request. The
                # original import deadline applies, not the short scroll budget.
                while challenge and time.monotonic() < deadline:
                    if cancel and cancel.is_set():
                        raise ValueError('Импорт отменён')
                    if page.is_closed():
                        raise ValueError('Окно Chrome закрыто до завершения импорта')
                    validate_url(page.url, resolve=False)
                    page.wait_for_timeout(500)
                    current = usable_snapshot(page.evaluate(snapshot_script))
                    ready, challenge, unavailable = listing_ready(current)
                if challenge:
                    raise ValueError('Время ручной проверки Chrome истекло. Повторите импорт.')
                emit(report,'Проверка завершена. Продолжаем догружать фотографии прокруткой…',stage='scroll',awaitingUser=False)
            if unavailable:
                raise ValueError('Объявление стало недоступно во время чтения.')
            if ready:
                snapshot = current
            images.extend(current.get('images', []))
            emit(report,'Прокрутка '+str(scrolls)+': найдено фото '+str(len(set(images)))+'. Проверяем конец страницы; предел '+str(max_scrolls)+' прохода.',stage='scroll',completed=scrolls)
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


def load_browser(url, profile, parse_html, validate_url, timeout=180, status=None, cancel=None,
                 channel='chrome', scroll_strategy='sections'):
    try:
        from playwright.sync_api import sync_playwright, Error, TimeoutError as BrowserTimeout
    except ImportError as error:
        raise ValueError('Для Chrome установите: python3 -m venv .venv && .venv/bin/python -m pip install -r cli/requirements-browser.txt\nЗатем: .venv/bin/python cli/redcn.py --browser --open') from error
    snapshot_script, section_probe = source_scripts(adapter_for(url))
    report = status
    if channel not in ('chrome','chromium'): raise ValueError('Неверный браузер')
    if scroll_strategy not in ('sections','page'): raise ValueError('Прокрутка Chrome: sections или page')
    browser_name = 'Google Chrome' if channel=='chrome' else 'Chromium / Chrome for Testing'
    if not _browser_lock.acquire(blocking=False):
        raise ValueError('Другое объявление уже открыто в Chrome. Дождитесь его загрузки или отмените импорт.')
    context = None
    try:
        validate_url(url)
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        emit(report,'Запускаем отдельное окно '+browser_name+'. Первый запуск браузера может быть дольше обычного. Ждём готовности окна, до 90 секунд.',stage='browser',maxTotalSeconds=90)
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(str(profile), channel='chrome' if channel=='chrome' else None, headless=False, accept_downloads=False, timeout=90000)
            except Error as error:
                hint = 'Откройте Google Chrome вручную один раз и закройте окно профиля redcn. Либо установите совместимый браузер: .venv/bin/python -m playwright install chromium и используйте --browser-channel chromium.' if channel=='chrome' else 'Установите браузер: .venv/bin/python -m playwright install chromium. Закройте другое окно профиля redcn.'
                raise ValueError('Не удалось открыть '+browser_name+'. '+hint+' '+str(error).splitlines()[0]) from error
            try:
                if cancel and cancel.is_set(): raise ValueError('Импорт отменён')
                emit(report,'Chrome открыт. Подключаемся к объявлению и ждём начальную страницу, до '+str(min(45,timeout))+' секунд…',stage='navigate',maxTotalSeconds=min(45,timeout))
                # This is a dedicated redcn context, never a CDP attachment to the
                # user's running Chrome. Do not overwrite a restored profile tab.
                page = context.new_page()
                page.set_default_timeout(3000)
                deadline = time.monotonic() + timeout
                try:
                    # Start inspecting the committed document rather than waiting
                    # for deferred third-party scripts or DOMContentLoaded.
                    response = page.goto(url, wait_until='commit', timeout=min(45000, timeout*1000))
                    if response and response.status == 404: raise ValueError('Объявление не найдено (404). Проверьте ссылку.')
                except BrowserTimeout:
                    emit(report,'Источник ещё не завершил начальную загрузку. Читаем уже появившиеся блоки в Chrome…',stage='navigate',maxTotalSeconds=timeout)
                last, stable, challenge_reported = '', 0, False
                pending_reported = False
                last_wait_report = 0
                section_capture = None
                section_attempted = False
                while time.monotonic() < deadline:
                    if cancel and cancel.is_set(): raise ValueError('Импорт отменён')
                    if page.is_closed(): raise ValueError('Окно Chrome закрыто до завершения импорта')
                    current_url = page.url
                    if current_url == 'about:blank':
                        if not pending_reported:
                            emit(report,'Chrome ещё не установил соединение с источником. Ждём ответ сайта, а не генерацию текста…',stage='navigate',maxTotalSeconds=timeout)
                            pending_reported = True
                        page.wait_for_timeout(1000)
                        continue
                    if current_url.startswith('chrome-error:'):
                        raise ValueError('Chrome не смог подключиться к источнику. Проверьте доступность ссылки в обычном браузере либо используйте --input. Настройки сертификатов не изменяются.')
                    validate_url(current_url, resolve=False)
                    try:
                        snapshot = page.evaluate(snapshot_script)
                    except Error as error:
                        message = str(error).lower()
                        if 'execution context was destroyed' in message or 'cannot find context' in message:
                            # A normal client redirect can replace the committed
                            # document between URL validation and the DOM read.
                            # Only these navigation-context errors are retried;
                            # the next loop revalidates URL/cancel/deadline.
                            page.wait_for_timeout(100)
                            continue
                        raise
                    # A selector can be renamed by a source; use the pruned rendered
                    # main section when it contains facts absent from the small blocks.
                    snapshot = usable_snapshot(snapshot)
                    ready, challenge, unavailable = listing_ready(snapshot)
                    if unavailable: raise ValueError('Объявление снято или недоступно. Вставьте сохранённый текст вручную.')
                    if challenge and (not challenge_reported or time.monotonic()-last_wait_report>=4):
                        emit(report,'Нужна ваша проверка в окне Chrome на Mac. Импорт ждёт её завершения; автоматически защита не обходится.',stage='challenge',awaitingUser=True)
                        challenge_reported = True
                        last_wait_report = time.monotonic()
                    elif not challenge and challenge_reported:
                        emit(report,'Проверка браузера завершена. Продолжаем чтение блоков квартиры…',stage='navigate',awaitingUser=False)
                        challenge_reported=False
                    if not challenge and scroll_strategy=='sections' and not section_attempted:
                        section_state = page.evaluate(section_probe, {'section':'gallery'})
                        if section_state.get('recognized'):
                            section_attempted = True
                            snapshot, section_capture = capture_sections(page, snapshot, deadline,
                                validate_url, status=report, cancel=cancel,
                                snapshot_script=snapshot_script, section_probe=section_probe)
                            ready, challenge, unavailable = listing_ready(snapshot)
                    current = snapshot['text'] or snapshot['fallback']
                    stable = stable + 1 if ready and current == last else 0
                    last = current
                    if not challenge and time.monotonic()-last_wait_report>=4:
                        if ready and section_capture and section_capture.get('completed'):
                            message='Нужные блоки уже загружены. Извлекаем данные без дополнительного ожидания.'
                        elif ready:
                            message='Цена, площадь и описание найдены. Проверяем, что данные перестали меняться: '+str(min(stable+1,3))+' из 3 чтений.'
                        else:
                            missing=[]
                            if not re.search(r'\d[\d\s\xa0,.]*\s*(?:м²|м2|кв\.?\s*м|㎡)',current,re.I):missing.append('площадь')
                            if not re.search(r'\d[\d\s\xa0]*\s*(?:₽|руб)',current,re.I):missing.append('цена')
                            if len(current)<80:missing.append('описание')
                            message='Страница открыта. Ждём блоки квартиры: '+(', '.join(missing) or 'полные данные')+'. Найдено кандидатов фото: '+str(len(snapshot.get('images',[])))+'.'
                        emit(report,message,stage='navigate',awaitingUser=False)
                        last_wait_report=time.monotonic()
                    if ready and (stable >= 2 or section_capture and section_capture.get('completed')):
                        from html import escape
                        if section_capture and section_capture.get('completed'):
                            capture = section_capture
                        else:
                            remaining = max(1, 12-(section_capture or {}).get('activeSeconds', 0))
                            emit(report, 'Некоторые блоки не распознаны или ещё загружаются. Проверяем страницу ограниченной прокруткой.', stage='scroll')
                            snapshot, capture = capture_scrolled(page, snapshot, deadline, validate_url,
                                status=report, cancel=cancel, budget=remaining, stage_budget=12, snapshot_script=snapshot_script)
                            capture['strategy'] = scroll_strategy
                            if section_capture:
                                capture['sections'] = section_capture['sections']
                                capture['sectionFallback'] = True
                        emit(report,'Читаем текст, цену, метро и найденные фотографии из загруженных блоков…',stage='extract')
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
                            emit(report,'Лимит прокрутки достигнут: сохранены доступные данные. Проверьте полноту фотографий в карточке.',stage='extract')
                        emit(report,'Объявление прочитано. Закрываем окно Chrome и передаём данные генератору…',stage='extract')
                        return result
                    page.wait_for_timeout(150 if scroll_strategy=='sections' and not challenge else 500)
                raise ValueError('Время ожидания Chrome истекло. Проверка или загрузка не завершилась. Повторите --browser --browser-timeout 300 либо вставьте текст вручную.')
            finally:
                context.close()
    except Error as error:
        raise ValueError('Chrome не прочитал страницу: '+str(error).splitlines()[0]) from error
    finally:
        _browser_lock.release()
