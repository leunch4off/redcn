"""Optional local MLX text worker; process exit releases the model after use.

The object detector remains separate. This worker reads only explicit text
messages and the selected local text-model artifacts, never source-page files.
Generated claims still need application-level checks against confirmed fields.
"""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from ai_telemetry import read_progress, sanitize_ai, write_progress
from job_progress import emit
from text_model_contract import (BASE_MODEL, EXPECTED_TRAINABLE, expected_parameters,
                                inference_model, file_sha, sampling_parameters)

MAX_INPUT = 40_000
MAX_OUTPUT = 100_000


def clean_request(request):
    if not isinstance(request, dict):
        raise ValueError('Нужен текстовый запрос')
    messages = request.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
        raise ValueError('Допустимо 1–8 текстовых сообщений')
    cleaned = []
    for row in messages:
        if not isinstance(row, dict) or row.get('role') not in ('system', 'user', 'assistant'):
            raise ValueError('Неизвестная роль сообщения')
        content = row.get('content')
        if not isinstance(content, str) or not content.strip() or len(content) > 12_000:
            raise ValueError('Сообщение должно содержать до 12000 символов')
        cleaned.append({'role': row['role'], 'content': content})
    tokens = request.get('maxTokens', 350)
    temperature = request.get('temperature', .75)
    if type(tokens) is not int or not 16 <= tokens <= 1024:
        raise ValueError('Нужно от 16 до 1024 токенов ответа')
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1.2:
        raise ValueError('Неверная температура генерации')
    result = {'messages': cleaned, 'maxTokens': tokens, 'temperature': temperature}
    if 'seed' in request:
        seed = request['seed']
        if type(seed) is not int or not 0 <= seed <= 4_294_967_295:
            raise ValueError('Неверное зерно генерации')
        result['seed'] = seed
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_INPUT:
        raise ValueError('Текстовый запрос слишком большой')
    return result


def settings(directory):
    base = Path(directory)
    path = base/'text-ai.json'
    value = {}
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= 16_000:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError('Неверные настройки текстовой модели')
    defaults = base/'tools'/'text-ai'
    return {'enabled': value.get('enabled') is True,
        'advertisingEnabled': value.get('advertisingEnabled') is True,
        'modelPath': value.get('modelPath', str(defaults/'model')),
        'pythonPath': value.get('pythonPath', str(defaults/'venv'/'bin'/'python')),
        'adapterPath': value.get('adapterPath') if value.get('useCandidateAdapter') is True else None,
        'maxSeconds': max(5, min(55, float(value.get('maxSeconds', 55))))}


def _response(status, message):
    return {'status': status, 'text': '', 'model': BASE_MODEL,
            'autoApply': False, 'message': message}


def _stop(process):
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                pass


def _progress_message(ai):
    return {'waiting': 'Текстовый ИИ: ожидаем свободную память',
        'loading': 'Текстовый ИИ: загружаем локальную модель',
        'preparing': 'Текстовый ИИ: готовим подтверждённые факты',
        'prefill': 'Текстовый ИИ: читаем запрос',
        'generating': 'Текстовый ИИ: создаём китайское описание',
        'complete': 'Текстовый ИИ: черновик готов, проверяем факты',
        'cancelled': 'Генерация текста отменена',
        'timeout': 'Время локальной генерации истекло',
        'unavailable': 'Текстовый ИИ сейчас недоступен',
        'error': 'Текстовый ИИ не смог завершить запрос'}.get(ai.get('phase'), 'Локальный текстовый ИИ')


def generate(request, directory, *, cancel=None, deadline=None, status=None):
    started = time.monotonic()
    process = None
    latest = {'backend': 'mlx', 'model': BASE_MODEL, 'operation': 'text', 'tokenBased': True}

    def report(measured):
        nonlocal latest
        latest = sanitize_ai({**latest, **measured, 'elapsedSeconds': max(0, time.monotonic()-started)})
        emit(status, _progress_message(latest), stage='generate', ai=latest.copy())

    def unavailable(message, *, phase='unavailable', result_status='unavailable'):
        report({'phase': phase})
        return {**_response(result_status, message), 'ai': latest.copy(),
            'elapsedSeconds': max(0, time.monotonic()-started)}

    try:
        request = clean_request(request)
        config = settings(directory)
        if not config['enabled']:
            return unavailable('Локальная текстовая модель выключена')
        python = Path(config['pythonPath'])
        model = Path(config['modelPath'])
        if not python.is_file() or not model.is_dir() or not (model/'config.json').is_file():
            return unavailable('Локальная текстовая модель ещё не установлена')
        identity=inference_model(json.loads((model/'config.json').read_text()))
        latest['model'] = identity
        budget = config['maxSeconds'] if deadline is None else min(config['maxSeconds'], deadline-time.monotonic())
        if budget <= 0 or (cancel is not None and cancel.is_set()):
            return unavailable('Время генерации истекло или запрос отменён',
                phase='cancelled' if cancel is not None and cancel.is_set() else 'timeout')
        report({'phase': 'waiting'})
        # Gate is shared with idle training: a foreground request pre-empts it.
        try:
            from learning_scheduler import model_resource, touch_foreground
            touch_foreground(directory)
            gate = model_resource(directory, kind='foreground', timeout=min(3, budget))
        except ImportError:
            gate = nullcontext()
        with gate, tempfile.TemporaryDirectory(prefix='redcn-ai-progress-') as progress_directory:
            try:
                progress_path = Path(progress_directory)/'progress.json'
                report({'phase': 'loading'})
                process = subprocess.Popen(['/usr/bin/arch', '-arm64', str(python),
                    str(Path(__file__).resolve()), '--worker', str(model),
                    str(config['adapterPath']) if config['adapterPath'] else '-', str(progress_path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                end = started+budget
                pending = json.dumps(request, ensure_ascii=False).encode()
                last_sample = None
                while True:
                    remaining = end-time.monotonic()
                    if remaining <= 0 or (cancel is not None and cancel.is_set()):
                        return unavailable('Локальная модель не успела ответить; исходные поля сохранены',
                            phase='cancelled' if cancel is not None and cancel.is_set() else 'timeout')
                    try:
                        stdout, _ = process.communicate(input=pending, timeout=min(.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pending = None
                        measured = read_progress(progress_path)
                        if measured and measured != last_sample:
                            report(measured)
                            last_sample = measured
                if process.returncode or len(stdout) > MAX_OUTPUT:
                    return unavailable('Локальная текстовая модель не смогла обработать запрос', phase='error')
                result = json.loads(stdout)
                if not isinstance(result, dict) or result.get('model') != identity or not isinstance(result.get('text'), str):
                    return unavailable('Неверный ответ локальной текстовой модели', phase='error')
                report({**sanitize_ai(result.get('ai')), 'phase': 'complete'})
                result['ai'] = latest.copy()
                result.update(status='review', autoApply=False, elapsedSeconds=time.monotonic()-started)
                return result
            finally:
                _stop(process)
    except Exception as error:
        if type(error).__name__ in ('BusyError', 'ModelBusy'):
            return unavailable('Локальная модель заканчивает предыдущую задачу. Повторите генерацию.', result_status='busy')
        if isinstance(error, (OSError, ValueError, TypeError, AttributeError)):
            return unavailable('Локальная текстовая модель недоступна; исходные поля сохранены')
        raise
    finally:
        _stop(process)


def worker(model_path, adapter_path, request, *, progress_path=None):
    started = time.monotonic()
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    request = clean_request(request)
    model_path = Path(model_path)
    model_config=json.loads((model_path/'config.json').read_text())
    identity=inference_model(model_config)
    metrics = {'backend': 'mlx', 'model': identity, 'operation': 'text', 'tokenBased': True}
    last_write = None

    def progress(phase, *, force=False, **measured):
        nonlocal last_write
        now = time.monotonic()
        metrics.update(phase=phase, elapsedSeconds=max(0, now-started), **measured)
        if force or last_write is None or now-last_write >= .25:
            write_progress(progress_path, metrics)
            last_write = now

    progress('loading', force=True)
    mx.set_memory_limit(2_500_000_000)
    mx.set_cache_limit(64_000_000)
    adapter_sha = None
    if adapter_path != '-':
        # A trained 0.5B adapter cannot be attached to the 1.5B candidate.
        expected_parameters(model_config)
        adapter = Path(adapter_path)
        receipt_path = adapter/'trainreceipt.json'
        if not receipt_path.is_file() or receipt_path.is_symlink() or receipt_path.stat().st_size > 2_000_000:
            raise ValueError('Missing adapter receipt')
        receipt = json.loads(receipt_path.read_text())
        weights = adapter/'adapters.safetensors'
        if (receipt.get('status') != 'trained-unaccepted' or receipt.get('trainableParameters') != EXPECTED_TRAINABLE
                or not receipt.get('stepsCompleted') or weights.is_symlink() or not weights.is_file()
                or not 0 < weights.stat().st_size < 8_000_000
                or receipt.get('baseConfigSha256') != file_sha(model_path/'config.json')
                or file_sha(weights) != receipt.get('adapterSha256')):
            raise ValueError('Incompatible adapter')
        adapter_sha = receipt['adapterSha256']
    model, tokenizer = load(str(model_path), adapter_path=None if adapter_path == '-' else adapter_path)
    progress('preparing', force=True)
    prompt = tokenizer.apply_chat_template(request['messages'], tokenize=False, add_generation_prompt=True)
    prompt_tokens = len(tokenizer.encode(prompt))
    if prompt_tokens > 2048:
        raise ValueError('Prompt exceeds the bounded local context')
    if 'seed' in request:
        mx.random.seed(request['seed'])
    progress('prefill', force=True, promptTokens=prompt_tokens,
        promptTokensProcessed=0, promptTokensTotal=prompt_tokens)

    def prefill(done, total):
        progress('prefill', promptTokensProcessed=done, promptTokensTotal=total)

    parts = []
    for response in stream_generate(model, tokenizer, prompt=prompt, max_tokens=request['maxTokens'],
            sampler=make_sampler(**sampling_parameters(request['temperature'])), prompt_progress_callback=prefill):
        parts.append(response.text)
        measured = {'generatedTokens': response.generation_tokens,
            'tokensPerSecond': response.generation_tps, 'promptTokens': response.prompt_tokens,
            'promptTokensPerSecond': response.prompt_tps}
        if response.generation_tps > 0:
            measured['generationSeconds'] = response.generation_tokens/response.generation_tps
        progress('generating', **measured)
    progress('complete', force=True)
    return {'status': 'review', 'text': ''.join(parts), 'model': identity, 'adapterSha256': adapter_sha,
        'ai': sanitize_ai(metrics),
        'autoApply': False, 'message': 'Локальный черновик: проверьте факты и китайскую формулировку перед публикацией.'}


if __name__ == '__main__':
    try:
        if len(sys.argv) not in (4, 5) or sys.argv[1] != '--worker':
            raise ValueError('Invalid worker arguments')
        raw = sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw) > MAX_INPUT:
            raise ValueError('Request exceeds limit')
        result = worker(sys.argv[2], sys.argv[3], json.loads(raw),
            progress_path=sys.argv[4] if len(sys.argv) == 5 else None)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
    except Exception:
        sys.exit(2)
