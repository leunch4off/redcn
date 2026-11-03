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
                                inference_model, file_sha, sampling_parameters,
                                inference_sampling_parameters, inference_memory_limit,
                                training_contract)

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
    purpose = request.get('purpose')
    if purpose not in (None, 'advertising'):
        raise ValueError('Неизвестное назначение текстового запроса')
    tokens = request.get('maxTokens', 350)
    temperature = request.get('temperature', .75)
    if type(tokens) is not int or not 16 <= tokens <= 1024:
        raise ValueError('Нужно от 16 до 1024 токенов ответа')
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1.2:
        raise ValueError('Неверная температура генерации')
    result = {'messages': cleaned, 'maxTokens': tokens, 'temperature': temperature}
    if 'maxPromptTokens' in request:
        if type(request['maxPromptTokens']) is not int or request['maxPromptTokens'] not in (2048, 4096):
            raise ValueError('Неверный предел контекста')
        result['maxPromptTokens'] = request['maxPromptTokens']
    if 'allowedStructurePlans' in request:
        from advertising_structure import validate_plans
        result['allowedStructurePlans'] = validate_plans(request['allowedStructurePlans'])[:]
    if 'seed' in request:
        seed = request['seed']
        if type(seed) is not int or not 0 <= seed <= 4_294_967_295:
            raise ValueError('Неверное зерно генерации')
        result['seed'] = seed
    if purpose:
        result['purpose'] = purpose
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
        'advertisingModelPath': value.get('advertisingModelPath'),
        'advertisingPythonPath': value.get('advertisingPythonPath'),
        'advertisingAdapterPath': value.get('advertisingAdapterPath') if value.get('useAdvertisingAdapter') is True else None,
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
        if request.get('purpose') == 'advertising' and config.get('advertisingModelPath'):
            config = {**config, 'modelPath': config['advertisingModelPath'],
                'pythonPath': config.get('advertisingPythonPath') or config['pythonPath'],
                'adapterPath': config.get('advertisingAdapterPath')}
        if not config['enabled']:
            return unavailable('Локальная текстовая модель выключена')
        python = Path(config['pythonPath'])
        model = Path(config['modelPath'])
        if not python.is_file() or not model.is_dir() or not (model/'config.json').is_file():
            return unavailable('Локальная текстовая модель ещё не установлена')
        identity=inference_model(json.loads((model/'config.json').read_text()))
        latest['model'] = identity
        end = started+config['maxSeconds']
        if deadline is not None:
            end = min(end, deadline)
        budget = end-time.monotonic()
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
        with gate as resource_fd, tempfile.TemporaryDirectory(prefix='redcn-ai-progress-') as progress_directory, \
                tempfile.TemporaryFile(mode='w+b') as request_file:
            try:
                progress_path = Path(progress_directory)/'progress.json'
                # A slow-starting MLX worker may not drain a pipe in the first
                # communicate(timeout=.1). Retrying with input=None then stops
                # writing the remaining input on CPython. Supply a finite file
                # before starting the child; polling now handles output only.
                request_file.write(json.dumps(request, ensure_ascii=False).encode())
                request_file.flush()
                request_file.seek(0)
                if time.monotonic() >= end or (cancel is not None and cancel.is_set()):
                    return unavailable('Время генерации истекло или запрос отменён',
                        phase='cancelled' if cancel is not None and cancel.is_set() else 'timeout')
                report({'phase': 'loading'})
                process = subprocess.Popen(['/usr/bin/arch', '-arm64', str(python),
                    str(Path(__file__).resolve()), '--worker', str(model),
                    str(config['adapterPath']) if config['adapterPath'] else '-', str(progress_path)],
                    stdin=request_file, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    pass_fds=(resource_fd,) if type(resource_fd) is int else ())
                last_sample = None
                while True:
                    remaining = end-time.monotonic()
                    if remaining <= 0 or (cancel is not None and cancel.is_set()):
                        return unavailable('Локальная модель не успела ответить; исходные поля сохранены',
                            phase='cancelled' if cancel is not None and cancel.is_set() else 'timeout')
                    try:
                        stdout, _ = process.communicate(timeout=min(.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
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
        for name in ('stdin', 'stdout', 'stderr'):
            stream = getattr(process, name, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


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
    memory_limit = inference_memory_limit(identity)
    mx.set_memory_limit(memory_limit)
    mx.set_cache_limit(64_000_000)
    adapter_sha = None
    if adapter_path != '-':
        adapter = Path(adapter_path)
        receipt_path = adapter/'trainreceipt.json'
        if not receipt_path.is_file() or receipt_path.is_symlink() or receipt_path.stat().st_size > 2_000_000:
            raise ValueError('Missing adapter receipt')
        receipt = json.loads(receipt_path.read_text())
        lane = receipt.get('trainingLane', 'translation')
        contract = training_contract(model_config, lane)
        weights = adapter/'adapters.safetensors'
        if (receipt.get('status') != 'trained-unaccepted' or receipt.get('trainableParameters') != contract['trainableParameters']
                or not receipt.get('stepsCompleted') or weights.is_symlink() or not weights.is_file()
                or not 0 < weights.stat().st_size < 8_000_000
                or receipt.get('baseConfigSha256') != file_sha(model_path/'config.json')
                or file_sha(weights) != receipt.get('adapterSha256')):
            raise ValueError('Incompatible adapter')
        if lane == 'advertising':
            adapter_config_path = adapter/'adapter_config.json'
            if (not adapter_config_path.is_file() or adapter_config_path.is_symlink()
                    or adapter_config_path.stat().st_size > 100_000):
                raise ValueError('Missing advertising adapter configuration')
            adapter_config = json.loads(adapter_config_path.read_text())
            if (request.get('purpose') != 'advertising' or receipt.get('trainingContract') != contract['name']
                    or receipt.get('baseModel') != contract['baseModel']
                    or receipt.get('layers') != contract['layers'] or receipt.get('lora') != contract['lora']
                    or receipt.get('stoppedEarly') is not False
                    or receipt.get('stepsCompleted') != receipt.get('limits', {}).get('steps')
                    or type(receipt.get('changedParameterElements')) is not int or receipt['changedParameterElements'] <= 0
                    or adapter_config.get('fine_tune_type') != 'lora'
                    or adapter_config.get('num_layers') != contract['layers']
                    or adapter_config.get('lora_parameters') != contract['lora']):
                raise ValueError('Unfinished or incompatible advertising adapter')
            from advertising_promotion import adapter_identity
            adapter_identity(adapter,model_path)
        adapter_sha = receipt['adapterSha256']
    model, tokenizer = load(str(model_path), adapter_path=None if adapter_path == '-' else adapter_path)
    progress('preparing', force=True)
    prompt = tokenizer.apply_chat_template(request['messages'], tokenize=False, add_generation_prompt=True,
        **({'enable_thinking': False} if request.get('purpose') == 'advertising' else {}))
    prompt_ids = list(tokenizer.encode(prompt))
    prompt_tokens = len(prompt_ids)
    if prompt_tokens > request.get('maxPromptTokens', 2048):
        raise ValueError('Prompt exceeds the bounded local context')
    if 'seed' in request:
        mx.random.seed(request['seed'])
    progress('prefill', force=True, promptTokens=prompt_tokens,
        promptTokensProcessed=0, promptTokensTotal=prompt_tokens)

    def prefill(done, total):
        progress('prefill', promptTokensProcessed=done, promptTokensTotal=total)

    parts = []
    finish_reason = None
    sampler_settings = inference_sampling_parameters(identity, request['temperature'])
    constrained = {}
    if 'allowedStructurePlans' in request:
        from advertising_structure import TokenChoiceConstraint
        constrained['logits_processors'] = [TokenChoiceConstraint(tokenizer,
            request['allowedStructurePlans'], prompt_ids)]
    for response in stream_generate(model, tokenizer, prompt=prompt, max_tokens=request['maxTokens'],
            sampler=make_sampler(**sampler_settings),
            prompt_progress_callback=prefill, **constrained):
        parts.append(response.text)
        reported_finish = getattr(response, 'finish_reason', None)
        if reported_finish in ('stop', 'length'):
            finish_reason = reported_finish
        measured = {'generatedTokens': response.generation_tokens,
            'tokensPerSecond': response.generation_tps, 'promptTokens': response.prompt_tokens,
            'promptTokensPerSecond': response.prompt_tps}
        if response.generation_tps > 0:
            measured['generationSeconds'] = response.generation_tokens/response.generation_tps
        progress('generating', **measured)
    progress('complete', force=True)
    peak_memory = getattr(mx, 'get_peak_memory', None)
    return {'status': 'review', 'text': ''.join(parts), 'model': identity, 'adapterSha256': adapter_sha,
        'stopReason': finish_reason or ('length' if metrics.get('generatedTokens', 0) >= request['maxTokens'] else 'stop'),
        'stopReasonSource': 'mlx-finish-reason' if finish_reason else 'token-bound-fallback',
        'sampling': sampler_settings, 'memoryLimitBytes': memory_limit,
        'peakMemoryBytes': int(peak_memory()) if callable(peak_memory) else None,
        'generationMode': 'bounded-structure-plan' if constrained else 'free-text',
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
