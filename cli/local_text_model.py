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
import time

from text_model_contract import BASE_MODEL, EXPECTED_TRAINABLE, expected_parameters, file_sha

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
    if type(tokens) is not int or not 16 <= tokens <= 512:
        raise ValueError('Нужно от 16 до 512 токенов ответа')
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1.2:
        raise ValueError('Неверная температура генерации')
    result = {'messages': cleaned, 'maxTokens': tokens, 'temperature': temperature}
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


def generate(request, directory, *, cancel=None, deadline=None):
    started = time.monotonic()
    process = None
    try:
        request = clean_request(request)
        config = settings(directory)
        if not config['enabled']:
            return _response('unavailable', 'Локальная текстовая модель выключена')
        python = Path(config['pythonPath'])
        model = Path(config['modelPath'])
        if not python.is_file() or not model.is_dir() or not (model/'config.json').is_file():
            return _response('unavailable', 'Локальная текстовая модель ещё не установлена')
        budget = config['maxSeconds'] if deadline is None else min(config['maxSeconds'], deadline-time.monotonic())
        if budget <= 0 or (cancel is not None and cancel.is_set()):
            return _response('unavailable', 'Время генерации истекло или запрос отменён')
        # Gate is shared with idle training: a foreground request pre-empts it.
        try:
            from learning_scheduler import model_resource, touch_foreground
            touch_foreground(directory)
            gate = model_resource(directory, kind='foreground', timeout=min(3, budget))
        except ImportError:
            gate = nullcontext()
        with gate:
            try:
                process = subprocess.Popen(['/usr/bin/arch', '-arm64', str(python),
                    str(Path(__file__).resolve()), '--worker', str(model),
                    str(config['adapterPath']) if config['adapterPath'] else '-'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                end = started+budget
                pending = json.dumps(request, ensure_ascii=False).encode()
                while True:
                    remaining = end-time.monotonic()
                    if remaining <= 0 or (cancel is not None and cancel.is_set()):
                        return _response('unavailable', 'Локальная модель не успела ответить; исходные поля сохранены')
                    try:
                        stdout, _ = process.communicate(input=pending, timeout=min(.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pending = None
                if process.returncode or len(stdout) > MAX_OUTPUT:
                    return _response('unavailable', 'Локальная текстовая модель не смогла обработать запрос')
                result = json.loads(stdout)
                if not isinstance(result, dict) or result.get('model') != BASE_MODEL or not isinstance(result.get('text'), str):
                    return _response('unavailable', 'Неверный ответ локальной текстовой модели')
                result.update(status='review', autoApply=False, elapsedSeconds=time.monotonic()-started)
                return result
            finally:
                _stop(process)
    except Exception as error:
        if type(error).__name__ in ('BusyError', 'ModelBusy'):
            return _response('busy', 'Локальная модель заканчивает предыдущую задачу. Повторите генерацию.')
        if isinstance(error, (OSError, ValueError, TypeError, AttributeError)):
            return _response('unavailable', 'Локальная текстовая модель недоступна; исходные поля сохранены')
        raise
    finally:
        _stop(process)


def worker(model_path, adapter_path, request):
    import mlx.core as mx
    from mlx_lm import load, generate as mlx_generate
    from mlx_lm.sample_utils import make_sampler
    request = clean_request(request)
    model_path = Path(model_path)
    expected_parameters(json.loads((model_path/'config.json').read_text()))
    mx.set_memory_limit(2_500_000_000)
    mx.set_cache_limit(64_000_000)
    adapter_sha = None
    if adapter_path != '-':
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
    prompt = tokenizer.apply_chat_template(request['messages'], tokenize=False, add_generation_prompt=True)
    if len(tokenizer.encode(prompt)) > 2048:
        raise ValueError('Prompt exceeds the bounded local context')
    response = mlx_generate(model, tokenizer, prompt=prompt, max_tokens=request['maxTokens'],
        sampler=make_sampler(temp=request['temperature'], top_p=.9), verbose=False)
    return {'status': 'review', 'text': response, 'model': BASE_MODEL, 'adapterSha256': adapter_sha,
        'autoApply': False, 'message': 'Локальный черновик: проверьте факты и китайскую формулировку перед публикацией.'}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 4 or sys.argv[1] != '--worker':
            raise ValueError('Invalid worker arguments')
        raw = sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw) > MAX_INPUT:
            raise ValueError('Request exceeds limit')
        result = worker(sys.argv[2], sys.argv[3], json.loads(raw))
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
    except Exception:
        sys.exit(2)
