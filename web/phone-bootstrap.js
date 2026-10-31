/* Independent ES5 startup guard: it must run even when the module cannot parse. */
(function () {
  'use strict';
  var version = 'v19', started = Date.now(), attempt = 0, timer = 0, done = false;
  var stage = 'script', route = '', phase = '', http = 0, code = '', errorType = '', line = 0, probe = '';
  var moduleStarted = false, probeTimer = 0, probing = false;
  function el(id) { return document.getElementById(id); }
  function text(id, value) { var node = el(id); if (node) node.textContent = value; }
  function show(id) { var node = el(id); if (node) node.classList.remove('hidden'); }
  function browser() {
    var ua = navigator.userAgent || '', ios = /(?:CPU (?:iPhone )?OS|iPhone OS) ([\d_]+)/.exec(ua), safari = /Version\/([\d.]+)/.exec(ua);
    return (ios ? 'iOS ' + ios[1].replace(/_/g, '.') : 'браузер') + (safari ? ' · Safari ' + safari[1] : '') + (navigator.standalone ? ' · с экрана Домой' : '');
  }
  function update() {
    var stages = {script: 'Запуск скрипта', initialization: 'Инициализация', connection: 'Связь с Mac', session: 'Проверка сессии', cards: 'Карточки', ready: 'Готово'}, phases = {request: 'ожидаем заголовки', body: 'читаем данные', complete: 'ответ получен'};
    text('phoneDiagnosticText', 'Версия ' + version + '\n' + browser() + '\nЭтап: ' + (stages[stage] || stage) + ' (' + stage + ')'  + (route ? ' · запрос: ' + route : '') + (phase ? ' · ' + (phases[phase] || phase) : '') + '\nВремя: ' + ((Date.now() - started) / 1000).toFixed(1) + ' с' + (http ? '\nHTTP: ' + http : '') + (code ? '\nКод: ' + code : '') + (errorType ? '\nJavaScript: ' + errorType + (line ? ', строка ' + line : '') : '') + (probe ? '\nПроверка сервера: ' + probe : '') + (navigator.onLine === false ? '\nБраузер сообщает: нет интернета' : ''));
  }
  function fail(reason, message) {
    if (done) return;
    done = true; clearTimeout(timer); code = reason; update();
    try { var stopped = document.createEvent('Event'); stopped.initEvent('redcn-phone-start-failed', false, false); window.dispatchEvent(stopped); } catch (ignore) {}
    var loading = el('phoneLoading'); if (loading) loading.setAttribute('data-result', 'error');
    var details = el('phoneLoadDetails'); if (details) details.open = true;
    text('phoneConnection', 'Подключение остановлено'); text('phoneLoadTitle', 'Не удалось завершить подключение');
    text('phoneLoadElapsed', ((Date.now() - started) / 1000).toFixed(1) + ' с');
    text('phoneLoadHelp', message); show('phoneLoadRetry'); if (el('phoneDiagnostics')) el('phoneDiagnostics').open = true;
    var retry = el('phoneLoadRetry'); if (retry) { retry.disabled = false; retry.onclick = reload; }
    var rowId = stage === 'cards' ? 'Cards' : stage === 'session' ? 'Session' : stage === 'script' ? 'Page' : 'Connection';
    var row = el('phoneLoad' + rowId); if (row) { row.setAttribute('data-state', 'error'); var mark = row.querySelector('.load-mark'); if (mark) mark.textContent = '!'; }
    text('phoneLoad' + rowId + 'Note', message);
  }
  function probeServer() {
    if (probing) return; probing = true;
    var xhr, ended = false;
    function finish(value) { if (ended) return; ended = true; probing = false; clearTimeout(probeTimer); probe = value; update(); }
    probe = 'проверяем отдельным запросом (до 4 с)'; update();
    try {
      xhr = new XMLHttpRequest(); xhr.open('GET', '/api/phone/state', true); xhr.setRequestHeader('Cache-Control', 'no-cache');
      xhr.onload = function () {
        var status = xhr.status, value = null;
        try { value = JSON.parse(xhr.responseText); } catch (ignore) {}
        if (status === 401) finish('Mac ответил HTTP 401 · для этого окна нужен код');
        else if (status === 200 && value && typeof value.paired === 'boolean') finish('Mac ответил HTTP 200 · ' + (value.paired ? 'сессия действует' : 'нужен код'));
        else finish('HTTP ' + status + (value ? ' · ответ приложения не распознан' : ' · вместо JSON получена другая страница'));
      };
      xhr.onerror = function () { finish('ответ не получен · сетевая ошибка, точная причина браузером не раскрыта'); };
      probeTimer = setTimeout(function () { finish('ответ не получен за 4 с · проверьте сеть, внешний адрес и сервер на Mac'); try { xhr.abort(); } catch (ignore) {} }, 4000);
      xhr.send();
    } catch (ignore) { finish('независимая проверка недоступна в этом браузере'); }
  }
  function arm() {
    clearTimeout(timer); var current = ++attempt;
    timer = setTimeout(function () { if (current !== attempt || done) return; fail(moduleStarted ? 'BOOT_STALLED' : 'BOOT_NOT_STARTED', moduleStarted ? 'Приложение не завершило подключение за 10 секунд. Ниже указан последний выполненный этап.' : 'Скрипт приложения не запустился за 10 секунд. Возможна несовместимость браузера или блокировка загрузки.'); probeServer(); }, 10000);
  }
  function reload() {
    // Keep the session and destination. Never clear cookies or private data.
    try { if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistration('/phone/').then(function (registration) { if (registration) registration.update().catch(function () {}); }).catch(function () {}); } catch (ignore) {}
    var parts = location.search.slice(1).split('&').filter(function (part) { return part && !/^reload=/.test(part); });
    parts.push('reload=' + Date.now()); location.replace(location.pathname + '?' + parts.join('&') + location.hash);
  }
  window.redcnPhoneBoot = {
    started: function () { moduleStarted = true; stage = 'initialization'; update(); },
    begin: function () { done = false; started = Date.now(); stage = 'connection'; route = ''; phase = ''; http = 0; code = ''; errorType = ''; line = 0; probe = ''; arm(); update(); },
    request: function (path, step, status) { if (done) return; var name = path.split('?')[0]; if (!/^(state|pair|cards)$/.test(name)) return; route = name; stage = name === 'cards' ? 'cards' : 'session'; phase = step; http = status || 0; update(); },
    finish: function (result, info) { done = true; clearTimeout(timer); info = info || {}; code = info.code || (result === 'ready' ? 'READY' : result === 'code' ? 'SESSION_REQUIRED' : 'CONNECTION_ERROR'); if (result === 'ready') stage = 'ready'; if (info.status) http = info.status; update(); }
  };
  window.addEventListener('error', function (event) {
    if (done || (!event.message && !(event.target && event.target.tagName === 'SCRIPT'))) return;
    var name = event.error && event.error.name; errorType = /^(TypeError|SyntaxError|ReferenceError|RangeError|Error)$/.test(name || '') ? name : 'ScriptError'; line = Number(event.lineno) || 0;
    fail('SCRIPT_ERROR', 'Браузер остановил запуск JavaScript. Версия, тип ошибки и проверка связи показаны ниже.'); probeServer();
  });
  window.addEventListener('unhandledrejection', function (event) {
    if (done) return; var name = event.reason && event.reason.name; errorType = /^(TypeError|SyntaxError|ReferenceError|RangeError|Error)$/.test(name || '') ? name : 'PromiseError';
    fail('SCRIPT_REJECTION', 'Запуск приложения прерван необработанной ошибкой. Ниже показаны этап и проверка связи.'); probeServer();
  });
  var copy = el('phoneDiagnosticCopy'); if (copy) copy.onclick = function () {
    var value = el('phoneDiagnosticText').textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(value).then(function () { text('phoneDiagnosticCopy', 'Скопировано'); }, function () { text('phoneDiagnosticCopy', 'Выделите текст диагностики ниже'); });
    else { var selection = window.getSelection(), range = document.createRange(); range.selectNodeContents(el('phoneDiagnosticText')); selection.removeAllRanges(); selection.addRange(range); text('phoneDiagnosticCopy', 'Текст выделен · выберите «Скопировать»'); }
  };
  var retry = el('phoneLoadRetry'); if (retry) retry.onclick = reload;
  window.addEventListener('pageshow', function () { if (!done && Date.now() - started > 10000) { fail('BOOT_STALLED', 'Подключение не завершилось после возврата в приложение. Повторите загрузку.'); probeServer(); } });
  update(); arm();
}());
