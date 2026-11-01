"""Small, factual AI progress records; never prompts, images or local paths.

Token rates come from token-generating backends. Image detectors do not have
tokens. A cache hit deliberately discards rates from the original inference.
"""
import json
import math
from pathlib import Path
import re
import tempfile


PHASES = frozenset(('waiting', 'loading', 'preparing', 'prefill', 'generating',
    'analyzing', 'complete', 'cached', 'cancelled', 'timeout', 'unavailable', 'error'))
ACTIVE_PHASES = frozenset(('waiting', 'loading', 'preparing', 'prefill', 'generating', 'analyzing'))
OPERATIONS = frozenset(('text', 'furniture', 'vision'))
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/+\-]{0,127}\Z')
_BACKEND = re.compile(r'[a-z0-9][a-z0-9_\-]{0,39}\Z')
_COUNTS = ('generatedTokens', 'promptTokens', 'promptTokensProcessed',
    'promptTokensTotal', 'processedImages', 'photoIndex', 'photoTotal')
_SECONDS = ('elapsedSeconds', 'generationSeconds', 'inferenceSeconds')
_RATES = ('tokensPerSecond', 'promptTokensPerSecond', 'imagesPerSecond')
_TOKEN_FIELDS = ('generatedTokens', 'generationSeconds', 'tokensPerSecond',
    'promptTokens', 'promptTokensPerSecond', 'promptTokensProcessed', 'promptTokensTotal')


def sanitize_ai(value):
    """Allowlist finite metadata, independently of a provider's response schema."""
    if not isinstance(value, dict):
        return {}
    result = {}
    backend = value.get('backend')
    if isinstance(backend, str) and _BACKEND.fullmatch(backend):
        result['backend'] = backend
    model = value.get('model')
    if isinstance(model, str) and _IDENTIFIER.fullmatch(model) and '://' not in model:
        result['model'] = model
    if isinstance(value.get('operation'), str) and value['operation'] in OPERATIONS:
        result['operation'] = value['operation']
    if isinstance(value.get('phase'), str) and value['phase'] in PHASES:
        result['phase'] = value['phase']
    if value.get('textSource') in ('model', 'rules'):
        result['textSource'] = value['textSource']
    for key in _COUNTS:
        number = value.get(key)
        if type(number) is int and 0 <= number <= 10_000_000:
            result[key] = number
    for key in _SECONDS + _RATES:
        number = value.get(key)
        limit = 7200 if key in _SECONDS else 1_000_000
        if type(number) in (int, float) and 0 <= number <= limit and math.isfinite(number):
            result[key] = round(number, 6)
    for key in ('cached', 'tokenBased'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    # A detector or a cached result cannot report fresh token generation.
    if result.get('tokenBased') is False or backend not in ('mlx', 'ollama'):
        for key in _TOKEN_FIELDS:
            result.pop(key, None)
    if result.get('cached') is True or result.get('phase') == 'cached':
        result.update(cached=True, phase='cached')
        for key in _TOKEN_FIELDS + ('inferenceSeconds', 'imagesPerSecond'):
            result.pop(key, None)
    return result


def write_progress(path, value):
    """Best-effort atomic sidecar; worker stdout remains one compatible JSON."""
    if path is None:
        return
    temporary = None
    try:
        path = Path(path)
        if path.is_symlink() or not path.parent.is_dir():
            return
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, encoding='utf-8', delete=False) as output:
            temporary = Path(output.name)
            json.dump(sanitize_ai(value), output, ensure_ascii=False, allow_nan=False)
        temporary.chmod(0o600)
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def read_progress(path):
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
            return {}
        return sanitize_ai(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return {}
