"""Measured native streams: no guessed token counts or repaired completions."""
import time


def runtime_error_kind(error):
    value = str(error).casefold()
    memory = ('out of memory', 'memory limit', 'failed to allocate',
              'insufficient memory', 'metal out of memory', 'allocation failed')
    return 'memory-error' if any(item in value for item in memory) else 'runtime-error'


def measure_stream(factory, *, maximum_tokens, probe_seconds, stop_reason=lambda: None):
    """Keep partial raw answers when runtime or limits stop a probe.

    A finished iterator alone is not called EOS: MLX versions may omit the
    finish reason. Token metrics must come from the actual generation response.
    """
    started = time.monotonic()
    chunks, last, stream, reason, error_type = [], None, None, None, None
    try:
        stream = factory()
        for item in stream:
            last = item
            chunks.append(item.text)
            reason = stop_reason()
            if reason or time.monotonic() - started > probe_seconds:
                reason = reason or 'time-limit'
                break
    except (RuntimeError, MemoryError, ValueError) as error:
        reason = 'memory-error' if isinstance(error, MemoryError) else runtime_error_kind(error)
        error_type = type(error).__name__
    finally:
        if stream is not None and hasattr(stream, 'close'):
            stream.close()
    count = getattr(last, 'generation_tokens', None)
    measured = type(count) is int and count >= 0
    finish = getattr(last, 'finish_reason', None)
    if reason is None:
        if not measured:
            reason = 'missing-runtime-metrics'
        elif count >= maximum_tokens or finish in ('length', 'max_tokens'):
            reason = 'token-limit'
        elif finish in ('stop', 'eos'):
            reason = 'eos'
        else:
            reason = 'stream-ended'
    complete = reason in ('eos', 'stream-ended') and measured and bool(''.join(chunks))
    result = {'text': ''.join(chunks), 'seconds': time.monotonic() - started,
              'generationTokens': count if measured else None,
              'generationTokensMeasured': measured, 'stopReason': reason,
              'status': 'measured' if complete else 'incomplete'}
    if error_type:
        result['runtimeErrorType'] = error_type
    tps = getattr(last, 'generation_tps', None)
    if type(tps) in (int, float) and tps >= 0:
        result['tokensPerSecond'] = tps
    return result
