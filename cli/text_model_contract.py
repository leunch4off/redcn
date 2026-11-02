"""Dependency-free contract for the optional local bilingual text adapter.

Weights are distinct from dictionary/example memory. Training is supervised by
approved or explicitly project-authored pairs; an output never approves itself.
"""
import hashlib
import json
import math
from pathlib import Path

BASE_MODEL = 'mlx-community/Qwen2.5-0.5B-Instruct-4bit'
INFERENCE_TOP_P = .9
INFERENCE_MODELS = {
    (896,24,14,2): BASE_MODEL,
    (1536,28,12,2): 'mlx-community/Qwen2.5-1.5B-Instruct-4bit',
}
LORA = {'rank': 8, 'scale': 16.0, 'dropout': 0.0,
        'keys': ['self_attn.q_proj', 'self_attn.v_proj']}
LAYERS = 24
EXPECTED_TRAINABLE = 540_672
MAX_DATASET_BYTES = 8_000_000


def sampling_parameters(temperature, *, top_p=INFERENCE_TOP_P):
    """One sampler contract for the production worker and paired evaluator."""
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1.2:
        raise ValueError('Invalid text sampling temperature')
    if type(top_p) not in (int, float) or top_p != INFERENCE_TOP_P:
        raise ValueError('Text evaluation must use the production top-p')
    return {'temp': temperature, 'top_p': INFERENCE_TOP_P}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def content_sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def expected_parameters(config):
    """Count actual rank-eight q/v projections, never dummy extra parameters."""
    if not isinstance(config, dict):
        raise ValueError('Model configuration is required')
    dimensions = tuple(config.get(k) for k in ('hidden_size', 'num_hidden_layers',
        'num_attention_heads', 'num_key_value_heads'))
    if dimensions != (896, 24, 14, 2) or config.get('model_type') != 'qwen2':
        raise ValueError('Only the pinned Qwen2.5 0.5B architecture is supported')
    hidden, layers, heads, kv_heads = dimensions
    return layers*LORA['rank']*((hidden+hidden)+(hidden+hidden//heads*kv_heads))


def inference_model(config):
    """Permit only the two researched text architectures for optional inference.

Training still uses the exact 0.5B contract above. Selecting a different base
does not make an existing adapter compatible or approve that model's quality.
"""
    if not isinstance(config,dict) or config.get('model_type') != 'qwen2':
        raise ValueError('Unsupported local text architecture')
    shape=tuple(config.get(key) for key in ('hidden_size','num_hidden_layers','num_attention_heads','num_key_value_heads'))
    if shape not in INFERENCE_MODELS:
        raise ValueError('Unsupported local text architecture')
    return INFERENCE_MODELS[shape]


def validate_dataset(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Training dataset version 1 is required')
    splits = value.get('splits')
    if not isinstance(splits, dict):
        raise ValueError('Separate train and validation splits are required')
    seen_groups, seen_contents, seen_inputs, seen_ids = set(), set(), set(), set()
    cleaned = {}
    for split in ('train', 'validation'):
        rows = splits.get(split)
        if not isinstance(rows, list) or not 2 <= len(rows) <= 10_000:
            raise ValueError('Each split needs 2–10000 supervised examples')
        current_groups, current_contents, current_inputs, result = set(), set(), set(), []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Invalid training record')
            key, group = row.get('id'), row.get('groupId')
            if not isinstance(key, str) or not key or key in seen_ids:
                raise ValueError('Training IDs must be unique')
            if not isinstance(group, str) or not group or len(group) > 128:
                raise ValueError('A stable property/lexical group is required')
            if group in seen_groups:
                raise ValueError('Group leaks across train and validation')
            if row.get('status') not in ('approved', 'project-authored'):
                raise ValueError('Unapproved examples cannot train weights')
            if not isinstance(row.get('source'), str) or not row['source']:
                raise ValueError('Example provenance is required')
            messages = row.get('messages')
            if not isinstance(messages, list) or len(messages) not in (2, 3):
                raise ValueError('Expected one user/assistant pair and optional system message')
            roles = [message.get('role') if isinstance(message, dict) else None for message in messages]
            if roles not in (['user', 'assistant'], ['system', 'user', 'assistant']):
                raise ValueError('Expected one user/assistant pair and optional system message')
            for message in messages:
                text = message.get('content')
                if not isinstance(text, str) or not text.strip() or len(text) > 6000:
                    raise ValueError('Training text must be bounded nonempty text')
            digest = content_sha(messages)
            input_digest = content_sha(messages[:-1])
            # Exact duplicates within either role are not independent examples.
            if digest in seen_contents or digest in current_contents:
                raise ValueError('Duplicate text across training records')
            if input_digest in seen_inputs:
                raise ValueError('The same input leaks across train and validation')
            current_groups.add(group); current_contents.add(digest); current_inputs.add(input_digest); seen_ids.add(key)
            result.append({**row, 'sha256': digest})
        cleaned[split] = result
        seen_groups.update(current_groups); seen_contents.update(current_contents); seen_inputs.update(current_inputs)
    return {**value, 'splits': cleaned}


def load_dataset(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_DATASET_BYTES:
        raise ValueError('Dataset must be a regular JSON file up to 8 MB')
    return validate_dataset(json.loads(path.read_text()))


def encode_record(tokenizer, row, max_length):
    """Only answer tokens receive loss; never silently discard a long answer."""
    messages = row['messages']
    prefix = tokenizer.apply_chat_template(messages[:-1], tokenize=True, add_generation_prompt=True)
    full = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
    prefix, full = list(prefix), list(full)
    if full[:len(prefix)] != prefix or not 0 < len(prefix) < len(full)-1:
        raise ValueError('Chat tokenizer does not expose a stable assistant boundary')
    if len(full) > max_length:
        return None
    return {'tokens': full, 'answerStart': len(prefix), 'id': row['id'], 'groupId': row['groupId']}


def answer_loss_mask(token_count, answer_start):
    """Targets are original positions 1..N-1, including the assistant terminator.

The tokenizer's final template suffix is explicit supervision too; it is not
silently trimmed. Native audit reports EOS and suffix positions separately.
"""
    if type(token_count) is not int or type(answer_start) is not int or not 1 <= answer_start < token_count:
        raise ValueError('Invalid supervised assistant boundary')
    return [position >= answer_start for position in range(1, token_count)]


def validation_gate(before, after):
    """Loss is only an experiment metric; human fact/language review is required."""
    finite = all(type(v) in (int, float) and math.isfinite(v) for v in (before, after))
    return {'lossImproved': bool(finite and after < before), 'autoPromote': False,
            'requiresHumanReview': True,
            'reason': 'Held-out loss does not establish factual accuracy, fluency or translation quality.'}
