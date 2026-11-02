"""Read a listing in an ordinary, visible Chrome session. Human checks stay human."""
import re
import threading
import time
from urllib.parse import urljoin
from job_progress import emit

_browser_lock = threading.Lock()

from browser_dom import source_scripts, gallery_scroll_script,gallery_sweep_script
from source_adapters import adapter_for
from gallery_coverage import gallery_coverage, observed_images,merge_gallery_items

SNAPSHOT, SECTION_PROBE = source_scripts(adapter_for('https://www.cian.ru/'))
GALLERY_SCROLL=gallery_scroll_script(adapter_for('https://www.cian.ru/'))
GALLERY_SWEEP=gallery_sweep_script(adapter_for('https://www.cian.ru/'))

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


def _checked_page(page, validate_url, cancel=None):
    if cancel and cancel.is_set():raise ValueError('Импорт отменён')
    if page.is_closed():raise ValueError('Окно Chrome закрыто до завершения импорта')
    validate_url(page.url, resolve=False)


def _browser_read(page, script, argument=None, *, kind, measurements):
    """Wall time includes browser scheduling/transport; JS reports its own work.

    serializationProbeMs times an explicit JSON.stringify, not Playwright's
    internal wire serializer. The two are deliberately not equated.
    """
    began=time.monotonic()
    result=page.evaluate(script) if argument is None else page.evaluate(script,argument)
    elapsed=max(0,time.monotonic()-began)
    row={'kind':kind,'seconds':round(elapsed,6)}
    if isinstance(result,dict):
        for key in ('domReadMs','serializationProbeMs','serializedChars'):
            value=result.get(key)
            if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0:row[key]=round(value,6)
    measurements.append(row)
    return result


def _section_reason(state):
    if state.get('ready'):return 'ready'
    if state.get('found') is False:return 'not_found'
    if state.get('found') is True:return 'partial'
    return 'not_visited_budget'


def capture_sections(page, initial, deadline, validate_url, status=None, cancel=None,
                     budget=6, section_wait=1.5, poll_ms=80,
                     snapshot_script=SNAPSHOT, section_probe=SECTION_PROBE,gallery_scroll=GALLERY_SCROLL,gallery_sweep=GALLERY_SWEEP):
    """Probe every section atomically, then spend a bounded follow-up budget.

    Initial all-section observations are made in SNAPSHOT itself. A single
    batched probe supports snapshots made by an earlier contract. An expensive
    gallery call may overrun a cooperative deadline; later sections never then
    inherit fictitious found=False values. No browser call is claimed to be
    preemptible while the source's main thread is busy.
    """
    snapshot=initial;images=list(initial.get('images',[]));started=time.monotonic()
    end=min(deadline,started+budget);manual_seconds=0;sections=[];measurements=[]
    kinds=('gallery','description','characteristics')
    labels={'gallery':'фотографии','description':'описание квартиры','characteristics':'характеристики, мебель и техника'}
    preflight=initial.get('sectionStates');from_snapshot=isinstance(preflight,dict) and all(kind in preflight for kind in kinds)
    captures=0;album_states=[initial.get('galleryState',{})];gallery_items=list(initial.get('galleryPhotos',[]));gallery_moves=0;gallery_restore=None;gallery_swept=False
    try:
        _checked_page(page,validate_url,cancel)
        if not from_snapshot:
            if time.monotonic()<end:
                batch=_browser_read(page,section_probe,{'section':'all'},kind='preflight',measurements=measurements)
                preflight=batch.get('sections') if isinstance(batch,dict) else None
                if not isinstance(preflight,dict) or not all(kind in preflight for kind in kinds):raise ValueError('Chrome не вернул первичную проверку всех разделов')
            else:preflight={}
        for index,section in enumerate(kinds):
            phase_started=time.monotonic();remaining=max(0,end-phase_started)
            # A gallery gets only its fair share of remaining follow-up time.
            phase_end=min(end,phase_started+min(section_wait,remaining/(len(kinds)-index)))
            known=preflight.get(section);state=dict(known) if isinstance(known,dict) else {'found':None,'ready':False}
            reads=1 if isinstance(known,dict) else 0
            moved=False;target_index=0;visited=set();calls_start=len(measurements)
            emit(status,'Переходим к блоку '+str(index+1)+' из 3: '+labels[section]+'. Первичная проверка всех разделов выполнена отдельно.',stage='scroll',section=section,maxTotalSeconds=budget)
            if section=='gallery':
                album=state.get('album') if isinstance(state.get('album'),dict) else initial.get('galleryState',{})
                album_states.append(album)
                coverage=gallery_coverage(album_states,images,photo_items=gallery_items)
                # The initial snapshot already found the carousel. One bounded
                # in-page sweep must precede another expensive transport/probe;
                # a busy source can otherwise consume all six seconds before
                # the first main slide is visited. The overall deadline wins.
                if isinstance(album,dict) and album.get('scrollable') and coverage['state']!='complete' and time.monotonic()<phase_end:
                    _checked_page(page,validate_url,cancel)
                    sweep=_browser_read(page,gallery_sweep,{'budgetMs':min(1800,max(1,(phase_end-time.monotonic())*1000)),'maxMoves':40},kind='gallery-sweep',measurements=measurements)
                    gallery_swept=True;gallery_moves+=sweep.get('moves',0)
                    images.extend(sweep.get('images',[]));gallery_items.extend(sweep.get('galleryPhotos',[]));album_states.extend(sweep.get('galleryStates',[]))
                    state['gallerySweepBounded']=sweep.get('bounded') is True
            while time.monotonic()<phase_end:
                _checked_page(page,validate_url,cancel)
                state=_browser_read(page,section_probe,{'section':section,'scroll':not moved,'index':target_index},kind='probe:'+section,measurements=measurements);reads+=1
                if state.get('found') and not moved:
                    moved=True;visited.add(target_index)
                    if not state.get('targetReady',state.get('ready')) and time.monotonic()<phase_end:
                        _browser_read(page,SECTION_FRAME,kind='frame:'+section,measurements=measurements)
                        if time.monotonic()<phase_end:
                            state=_browser_read(page,section_probe,{'section':section,'index':target_index},kind='probe:'+section,measurements=measurements);reads+=1
                # Never start a heavy clone/serialization after the shared limit.
                if time.monotonic()>=end:break
                current=usable_snapshot(_browser_read(page,snapshot_script,kind='snapshot:'+section,measurements=measurements));captures+=1
                ready,challenge,unavailable=listing_ready(current)
                if challenge:
                    challenged_at=time.monotonic()
                    emit(status,'Источник запросил проверку. Пройдите её вручную в Chrome; чтение блоков приостановлено.',stage='challenge',awaitingUser=True)
                    while challenge and time.monotonic()<deadline:
                        _checked_page(page,validate_url,cancel)
                        page.wait_for_timeout(min(500,max(1,int((deadline-time.monotonic())*1000))))
                        current=usable_snapshot(_browser_read(page,snapshot_script,kind='challenge',measurements=measurements));captures+=1
                        ready,challenge,unavailable=listing_ready(current)
                    if challenge:raise ValueError('Время ручной проверки Chrome истекло. Повторите импорт.')
                    delay=time.monotonic()-challenged_at;manual_seconds+=delay;end=min(deadline,end+delay);phase_end=min(end,phase_end+delay);moved=False
                    emit(status,'Проверка завершена. Продолжаем чтение блока: '+labels[section]+'.',stage='scroll',section=section,awaitingUser=False)
                    continue
                if unavailable:raise ValueError('Объявление стало недоступно во время чтения.')
                if ready:snapshot=current
                images.extend(current.get('images',[]))
                gallery_items.extend(current.get('galleryPhotos',[]));gallery_items=gallery_items[-1200:]
                if section=='gallery':album_states.append(current.get('galleryState',{}))
                # A full read can verify newly loaded later sections too.
                observed=current.get('sectionStates')
                if isinstance(observed,dict):
                    # The full snapshot is newer than the preceding lightweight
                    # probe; do not discard readiness that changed during it.
                    if isinstance(observed.get(section),dict):state=observed[section]
                    for future in kinds[index+1:]:
                        if isinstance(observed.get(future),dict):preflight[future]=observed[future]
                if section=='gallery':
                    album=state.get('album',{}) if isinstance(state.get('album'),dict) else {}
                    album_states.append(album)
                    coverage=gallery_coverage(album_states,images,photo_items=gallery_items)
                    if album.get('scrollable') and not gallery_swept and time.monotonic()<phase_end and coverage['state']!='complete':
                        sweep=_browser_read(page,gallery_sweep,{'budgetMs':min(1800,max(1,(phase_end-time.monotonic())*1000)),'maxMoves':40},kind='gallery-sweep',measurements=measurements)
                        gallery_swept=True;gallery_moves+=sweep.get('moves',0)
                        images.extend(sweep.get('images',[]));gallery_items.extend(sweep.get('galleryPhotos',[]));album_states.extend(sweep.get('galleryStates',[]))
                        state['gallerySweepBounded']=sweep.get('bounded') is True
                if state.get('ready'):break
                if state.get('targetReady') and isinstance(state.get('nextPendingIndex'),int) and state['nextPendingIndex']>=0:
                    target_index=state['nextPendingIndex'];moved=False;continue
                remaining=phase_end-time.monotonic()
                if remaining>0:page.wait_for_timeout(min(poll_ms,max(1,int(remaining*1000))))
            reason=_section_reason(state)
            result={'section':section,**state,'reason':reason,'visited':bool(visited),'reads':reads,'blocksVisited':len(visited),
                    'seconds':round(time.monotonic()-phase_started,3),
                    'browserCallSeconds':round(sum(row['seconds'] for row in measurements[calls_start:]),6)}
            sections.append(result)
            note={'ready':'Блок готов: ','not_found':'При проверке блок не найден: ','partial':'Блок пока неполный: ','not_visited_budget':'Блок не проверен: исчерпан бюджет — '}[reason]
            emit(status,note+labels[section]+'. Найдено кандидатов фото: '+str(len(set(images)))+'.',stage='scroll',section=section,completed=len(sections),sectionReady=reason=='ready',sectionState=reason)
    finally:
        if not page.is_closed():
            if gallery_restore is not None:_browser_read(page,gallery_scroll,{'restore':gallery_restore},kind='restore-gallery-scroll',measurements=measurements)
            _browser_read(page,SCROLL_TOP,kind='restore-scroll',measurements=measurements)
    elapsed=max(0,time.monotonic()-started)
    coverage=gallery_coverage(album_states,images,photo_items=gallery_items)
    sections_complete=len(sections)==3 and all(row.get('ready') for row in sections) and (from_snapshot or captures>0)
    complete=sections_complete and coverage['state']=='complete' and not coverage['overLimit']
    return {**snapshot,'images':list(dict.fromkeys(images))[:180],'galleryPhotos':gallery_items}, {
        'version':3,'strategy':'sections','sections':sections,'completed':complete,'sectionsCompleted':sections_complete,'bounded':not complete,'pageBottomReached':False,
        'scrolls':sum(row['blocksVisited'] for row in sections),'imageCandidates':len(set(images)),
        'seconds':round(elapsed,3),'activeSeconds':round(max(0,elapsed-manual_seconds),3),
        'budgetSeconds':budget,'budgetOverrunSeconds':round(max(0,elapsed-manual_seconds-budget),3),
        'preflightSource':'snapshot' if from_snapshot else 'batched-probe','fullSnapshotReads':captures,'browserCalls':measurements,
        'browserCallSeconds':round(sum(row['seconds'] for row in measurements),6),
        'galleryCoverage':coverage,'galleryMoves':gallery_moves,
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
    measurements=[];started=time.monotonic()
    snapshot = initial
    images = list(initial.get('images', []));album_states=[initial.get('galleryState',{})];gallery_items=list(initial.get('galleryPhotos',[]))
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
            position = _browser_read(page,SCROLL,kind='page-scroll',measurements=measurements)
            scrolls += 1
            page.wait_for_timeout(min(350, max(1, int((end-time.monotonic())*1000))))
            current = usable_snapshot(_browser_read(page,snapshot_script,kind='page-snapshot',measurements=measurements))
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
                    current = usable_snapshot(_browser_read(page,snapshot_script,kind='page-snapshot',measurements=measurements))
                    ready, challenge, unavailable = listing_ready(current)
                if challenge:
                    raise ValueError('Время ручной проверки Chrome истекло. Повторите импорт.')
                emit(report,'Проверка завершена. Продолжаем догружать фотографии прокруткой…',stage='scroll',awaitingUser=False)
            if unavailable:
                raise ValueError('Объявление стало недоступно во время чтения.')
            if ready:
                snapshot = current
            images.extend(current.get('images', []))
            gallery_items.extend(current.get('galleryPhotos',[]));gallery_items=gallery_items[-1200:]
            album_states.append(current.get('galleryState',{}))
            emit(report,'Прокрутка '+str(scrolls)+': найдено фото '+str(len(set(images)))+'. Проверяем конец страницы; предел '+str(max_scrolls)+' прохода.',stage='scroll',completed=scrolls)
            signature = (position.get('height'), tuple(dict.fromkeys(images)),
                         listing_signature(current))
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
            _browser_read(page,SCROLL_TOP,kind='restore-scroll',measurements=measurements)
    coverage=gallery_coverage(album_states,images,photo_items=gallery_items)
    complete=bottom_stable>=2 and coverage['state']=='complete' and not coverage['overLimit']
    return {**snapshot, 'images': list(dict.fromkeys(images))[:180],'galleryPhotos':gallery_items}, {
        'version': 1, 'scrolls': scrolls, 'completed': complete,'pageWalkCompleted':bottom_stable>=2,
        'bounded': not complete, 'imageCandidates': len(set(images)),
        'seconds':round(max(0,time.monotonic()-started),3),'browserCalls':measurements,
        'browserCallSeconds':round(sum(row['seconds'] for row in measurements),6),
        'galleryCoverage':coverage,
    }


def listing_signature(snapshot):
    semantic=snapshot.get('stabilityKey')
    text=semantic if isinstance(semantic,str) and semantic else snapshot.get('text') or snapshot.get('fallback','')
    states=snapshot.get('sectionStates') or {}
    checked=tuple((kind,_section_reason(states[kind])) for kind in ('gallery','description','characteristics') if isinstance(states.get(kind),dict))
    return (text,tuple(snapshot.get('images') or ()),checked)


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
    config=adapter_for(url)
    snapshot_script, section_probe = source_scripts(config)
    gallery_scroll=gallery_scroll_script(config)
    gallery_sweep=gallery_sweep_script(config)
    report = status
    if channel not in ('chrome','chromium'): raise ValueError('Неверный браузер')
    if scroll_strategy not in ('sections','page'): raise ValueError('Прокрутка Chrome: sections или page')
    browser_name = 'Google Chrome' if channel=='chrome' else 'Chromium / Chrome for Testing'
    if not _browser_lock.acquire(blocking=False):
        raise ValueError('Другое объявление уже открыто в Chrome. Дождитесь его загрузки или отмените импорт.')
    context = None
    load_calls=[];browser_started=time.monotonic();browser_start_seconds=0;navigation_seconds=0
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
            browser_start_seconds=max(0,time.monotonic()-browser_started)
            try:
                if cancel and cancel.is_set(): raise ValueError('Импорт отменён')
                emit(report,'Chrome открыт. Подключаемся к объявлению и ждём начальную страницу, до '+str(min(45,timeout))+' секунд…',stage='navigate',maxTotalSeconds=min(45,timeout))
                # This is a dedicated redcn context, never a CDP attachment to the
                # user's running Chrome. Do not overwrite a restored profile tab.
                page = context.new_page()
                page.set_default_timeout(3000)
                deadline = time.monotonic() + timeout
                navigation_started=time.monotonic()
                try:
                    # Start inspecting the committed document rather than waiting
                    # for deferred third-party scripts or DOMContentLoaded.
                    response = page.goto(url, wait_until='commit', timeout=min(45000, timeout*1000))
                    if response and response.status == 404: raise ValueError('Объявление не найдено (404). Проверьте ссылку.')
                except BrowserTimeout:
                    emit(report,'Источник ещё не завершил начальную загрузку. Читаем уже появившиеся блоки в Chrome…',stage='navigate',maxTotalSeconds=timeout)
                navigation_seconds=max(0,time.monotonic()-navigation_started)
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
                        snapshot = _browser_read(page,snapshot_script,kind='navigation-snapshot',measurements=load_calls)
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
                        section_state = _browser_read(page,section_probe,{'section':'gallery'},kind='layout-recognition',measurements=load_calls)
                        if section_state.get('recognized'):
                            section_attempted = True
                            snapshot, section_capture = capture_sections(page, snapshot, deadline,
                                validate_url, status=report, cancel=cancel,
                                snapshot_script=snapshot_script, section_probe=section_probe,gallery_scroll=gallery_scroll,gallery_sweep=gallery_sweep)
                            ready, challenge, unavailable = listing_ready(snapshot)
                    current = snapshot['text'] or snapshot['fallback']
                    fingerprint=listing_signature(snapshot)
                    stable = stable + 1 if ready and fingerprint == last else 0
                    last = fingerprint
                    if not challenge and time.monotonic()-last_wait_report>=4:
                        if ready and section_capture and section_capture.get('completed'):
                            message='Нужные блоки уже загружены. Извлекаем данные без дополнительного ожидания.'
                        elif ready:
                            message='Цена и площадь найдены. Проверяем стабильность фактов; полнота описания и характеристик учитывается отдельно: '+str(min(stable+1,3))+' из 3 чтений.'
                        else:
                            missing=[]
                            if not re.search(r'\d[\d\s\xa0,.]*\s*(?:м²|м2|кв\.?\s*м|㎡)',current,re.I):missing.append('площадь')
                            if not re.search(r'\d[\d\s\xa0]*\s*(?:₽|руб)',current,re.I):missing.append('цена')
                            if len(current)<80:missing.append('достаточный объём текста')
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
                                capture['sectionFallback'] = True
                                capture['pageWalkCompleted']=capture.get('pageWalkCompleted',bool(capture.get('completed')))
                                observed=snapshot.get('sectionStates') or {}
                                checked=[]
                                for previous in section_capture['sections']:
                                    state=observed.get(previous['section'])
                                    row=dict(previous)
                                    if isinstance(state,dict):
                                        row.update(state);row['reason']=_section_reason(state);row['reads']+=1;row['postFallbackObserved']=True
                                    checked.append(row)
                                capture['sections']=checked
                                capture['sectionsCompleted']=len(checked)==3 and all(row.get('ready') for row in checked)
                                capture['completed']=capture['sectionsCompleted'] and capture['galleryCoverage']['state']=='complete' and not capture['galleryCoverage']['overLimit']
                                capture['bounded']=bool(capture.get('bounded')) or not capture['completed']
                                capture['version']=3
                                for key in ('preflightSource','fullSnapshotReads','budgetOverrunSeconds','budgetSeconds'):
                                    capture[key]=section_capture[key]
                                capture['sectionSeconds']=section_capture['seconds']
                                capture['sectionActiveSeconds']=section_capture['activeSeconds']
                                capture['browserCalls']=section_capture['browserCalls']+capture.get('browserCalls',[])
                                capture['browserCallSeconds']=round(sum(row['seconds'] for row in capture['browserCalls']),6)
                        emit(report,'Читаем текст, цену, метро и найденные фотографии из загруженных блоков…',stage='extract')
                        current = snapshot['text'] or snapshot.get('fallback', '')
                        result = parse_html(snapshot.get('metadata','')+snapshot.get('sourceHtml','')+'<p>'+escape(current)+'</p>'+snapshot.get('relatedHtml',''), url)
                        # Visible summary includes price, floor and transit absent from JSON-LD.
                        result['text'] = current[:40000]
                        known=result.get('photoItems',[])
                        structured=len([row for row in known if isinstance(row,dict) and row.get('source')=='CIAN offer']) if isinstance(known,list) else 0
                        result['photoItems']=merge_gallery_items(known,snapshot.get('galleryPhotos',[]),validate_url,page.url,result.get('layouts',[]))
                        if isinstance(result.get('fields'),dict) and result['photoItems']:result['fields']['photoItems']=result['photoItems']
                        collected=observed_images([*result.get('images',[]),*snapshot.get('images',[])],validate_url,page.url,result.get('photoItems',[]),result.get('layouts',[]))
                        album_states=[snapshot.get('galleryState',{}),capture.get('galleryCoverage',{})]
                        capture['galleryCoverage']=gallery_coverage(album_states,collected,structured_count=structured or None,photo_items=result['photoItems'])
                        capture['completed']=bool(capture.get('sectionsCompleted',capture.get('pageWalkCompleted',capture.get('completed')))) and capture['galleryCoverage']['state']=='complete' and not capture['galleryCoverage']['overLimit']
                        capture['bounded']=not capture['completed']
                        result['images'] = collected[:30]
                        result['loader'] = 'chrome'
                        result['browser_channel'] = channel
                        capture['stabilityMethod']='adapter-fields' if snapshot.get('stabilityKey') else 'fallback-text'
                        capture['loadTimings']={'browserStartSeconds':round(browser_start_seconds,6),'navigationSeconds':round(navigation_seconds,6),'browserCalls':load_calls,'browserCallSeconds':round(sum(row['seconds'] for row in load_calls),6)}
                        result['capture'] = capture
                        coverage=capture['galleryCoverage']
                        if coverage['missing'] or coverage['overLimit'] or coverage.get('previewOnly'):
                            emit(report,'Альбом неполный: найдено '+str(coverage['observed'])+' из '+str(coverage['expected'] or '?')+' фото; сохранено ссылок '+str(coverage['retained'])+'. Пропущено '+str(coverage['missing'] or 0)+', только миниатюры '+str(coverage.get('previewOnly',0))+', сверх лимита '+str(coverage['overLimit'])+'.',stage='extract',albumPartial=True)
                        elif coverage['state']=='unknown':
                            emit(report,'Найдено '+str(coverage['observed'])+' фото. Полнота альбома не подтверждена: надёжный общий счётчик отсутствует или противоречив.',stage='extract',albumPartial=False)
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
