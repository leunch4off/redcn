"""Offline, bounded native-MLX supervised adaptation; never promotes a model.

Install mlx-lm into an isolated arm64 Python environment. The local model must
already exist: this script cannot download anything. Only 540672 real q/v LoRA
parameters are optimized; immutable base weights stay frozen. Same-directory
resume restores adapter and optimizer together after validating their receipts.
"""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from text_model_contract import (BASE_MODEL, EXPECTED_TRAINABLE, LAYERS, LORA,
    canonical, content_sha, encode_record, expected_parameters, file_sha,
    load_dataset, validation_gate)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    os.replace(temporary, path)


def training_row(rows, *, seed, step, sampling='random'):
    """A step determines its row without mutable RNG state across resumes."""
    if sampling == 'random':
        return random.Random(seed+step).choice(rows)
    if sampling != 'shuffled':
        raise ValueError('Unknown training sampling mode')
    epoch, offset = divmod(step, len(rows))
    order = list(range(len(rows)))
    random.Random(seed+epoch).shuffle(order)
    return rows[order[offset]]


def resume_receipt(output, args, model_path):
    output = Path(output)
    receipt_path = output/'trainreceipt.json'
    if not receipt_path.exists() and not (output/'adapters.safetensors').exists():
        return None
    if not args.resume_adapter or Path(args.resume_adapter).resolve() != (output/'adapters.safetensors').resolve():
        raise ValueError('Use a new output directory or explicitly resume its checkpoint')
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('Missing checkpoint receipt')
    value = json.loads(receipt_path.read_text())
    expected = {'datasetSha256': file_sha(args.dataset), 'baseConfigSha256': file_sha(Path(model_path)/'config.json'),
        'trainableParameters': EXPECTED_TRAINABLE, 'seed': args.seed, 'lora': LORA}
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError('Checkpoint dataset, model, seed or adapter configuration changed')
    if value.get('sampling', 'random') != getattr(args, 'sampling', 'random'):
        raise ValueError('Checkpoint sampling mode changed')
    if value.get('limits', {}).get('maxSequenceLength') != args.max_seq_length or value.get('learningRate') != args.learning_rate:
        raise ValueError('Checkpoint token limit or learning rate changed')
    for filename, key in [('adapters.safetensors','adapterSha256'),
            ('optimizer.safetensors','optimizerSha256'), ('initial.safetensors','initialSha256')]:
        path = output/filename
        if path.is_symlink() or not path.is_file() or file_sha(path) != value.get(key):
            raise ValueError('Checkpoint hashes are inconsistent')
    return value


def run(args):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Training requires native arm64 Python on an Apple Silicon Mac')
    model_path = Path(args.model).resolve()
    if not model_path.is_dir() or not (model_path/'config.json').is_file():
        raise ValueError('A downloaded local MLX model directory is required')
    config = json.loads((model_path/'config.json').read_text())
    count = expected_parameters(config)
    if count != EXPECTED_TRAINABLE:
        raise ValueError('Architecture has an unexpected adapter parameter count')
    data = load_dataset(args.dataset)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    previous = resume_receipt(output, args, model_path)
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten, tree_unflatten
    from mlx_lm import load
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from mlx_lm.tuner.trainer import grad_checkpoint
    # One process, one model, short contexts. OS pressure still needs supervising.
    mx.set_memory_limit(3_500_000_000)
    mx.set_cache_limit(128_000_000)
    mx.random.seed(args.seed)
    start = time.monotonic()
    cancel_signal = [False]
    signal.signal(signal.SIGTERM, lambda *_: cancel_signal.__setitem__(0, True))
    model, tokenizer = load(str(model_path))
    model.freeze()
    linear_to_lora_layers(model, LAYERS, LORA)
    actual = dict(tree_flatten(model.trainable_parameters()))
    trainable = sum(int(array.size) for array in actual.values())
    if trainable != EXPECTED_TRAINABLE or len(actual) != LAYERS*4:
        raise ValueError(f'Expected {EXPECTED_TRAINABLE} adapter weights, got {trainable}')
    if args.resume_adapter:
        adapter = Path(args.resume_adapter)
        if not adapter.is_file() or adapter.is_symlink():
            raise ValueError('Resume adapter must be a regular safetensors file')
        model.load_weights(str(adapter), strict=False)
    mx.eval(model.parameters())
    # Copy before any update; MLX arrays are immutable values.
    initial = (mx.load(str(output/'initial.safetensors')) if previous else
        {key: value for key, value in tree_flatten(model.trainable_parameters())})
    if not previous:
        mx.save_safetensors(str(output/'initial.safetensors'), initial)
    encoded, skipped = {}, {}
    for split in ('train', 'validation'):
        encoded[split], skipped[split] = [], []
        for row in data['splits'][split]:
            item = encode_record(tokenizer, row, args.max_seq_length)
            if item is None:
                skipped[split].append(row['id'])
            else:
                encoded[split].append(item)
        if len(encoded[split]) < 2:
            raise ValueError(f'{split} has fewer than two complete examples within the token limit')

    def loss_fn(current, tokens, answer_start):
        inputs, targets = tokens[:, :-1], tokens[:, 1:]
        logits = current(inputs)
        mask = mx.arange(1, tokens.shape[1]) >= answer_start
        losses = nn.losses.cross_entropy(logits, targets)
        return (losses.astype(mx.float32)*mask).sum()/mask.sum()

    validation_rows = encoded['validation'][:args.eval_examples]

    def evaluate():
        model.eval()
        values = []
        for row in validation_rows:
            result = loss_fn(model, mx.array([row['tokens']]), row['answerStart'])
            mx.eval(result)
            values.append(float(result.item()))
        return sum(values)/len(values)

    before = previous['baselineValidationLoss'] if previous else evaluate()
    grad_checkpoint(model.layers[0])
    optimizer = optim.Adam(learning_rate=args.learning_rate)
    if previous:
        optimizer.state = tree_unflatten(list(mx.load(str(output/'optimizer.safetensors')).items()))
    gradient = nn.value_and_grad(model, loss_fn)
    receipt = {'version': 1, 'status': 'training', 'baseModel': BASE_MODEL,
        'baseConfigSha256': file_sha(model_path/'config.json'),
        'baseArtifacts': {path.name:file_sha(path) for path in sorted(model_path.glob('*.safetensors'))
            if path.is_file() and not path.is_symlink()},
        'datasetSha256': file_sha(args.dataset), 'datasetContentSha256': content_sha(data),
        'trainableParameters': trainable, 'adapterTensorCount': len(actual),
        'lora': LORA, 'layers': LAYERS, 'seed': args.seed, 'sampling': args.sampling,
        'learningRate': args.learning_rate, 'initialSha256': file_sha(output/'initial.safetensors'),
        'packages': {key: importlib.metadata.version(key) for key in ('mlx', 'mlx-lm')},
        'limits': {'seconds': args.max_seconds, 'steps': args.steps, 'maxSequenceLength': args.max_seq_length,
            'memoryBytes': 3_500_000_000, 'batchSize': 1},
        'examples': {key: len(rows) for key, rows in encoded.items()},
        'skippedTooLongIds': skipped, 'validationExampleIds': [row['id'] for row in validation_rows],
        'baselineValidationLoss': before, 'stepsCompleted': previous['stepsCompleted'] if previous else 0,
        'losses': previous['losses'] if previous else [], 'previousElapsedSeconds': previous.get('elapsedSeconds', 0) if previous else 0,
        'autoPromote': False, 'resumeSemantics': 'same directory: exact adapter/optimizer; new directory: adapter warm start'}
    atomic_json(output/'adapter_config.json', {'fine_tune_type': 'lora',
        'num_layers': LAYERS, 'lora_parameters': LORA, 'model': str(model_path)})
    atomic_json(output/'trainreceipt.json', receipt)
    print(canonical({'status': 'training', 'parameters': trainable,
        'examples': receipt['examples'], 'baselineLoss': before}), flush=True)
    interrupted = False

    def checkpoint():
        temporary = output/'adapters.tmp.safetensors'
        mx.save_safetensors(str(temporary), dict(tree_flatten(model.trainable_parameters())))
        os.replace(temporary, output/'adapters.safetensors')
        optimizer_temporary = output/'optimizer.tmp.safetensors'
        mx.save_safetensors(str(optimizer_temporary), dict(tree_flatten(optimizer.state)))
        os.replace(optimizer_temporary, output/'optimizer.safetensors')
        receipt['adapterSha256'] = file_sha(output/'adapters.safetensors')
        receipt['optimizerSha256'] = file_sha(output/'optimizer.safetensors')
        receipt['elapsedSeconds'] = receipt['previousElapsedSeconds']+time.monotonic()-start
        atomic_json(output/'trainreceipt.json', receipt)

    try:
        for step in range(receipt['stepsCompleted'], args.steps):
            if cancel_signal[0] or time.monotonic()-start >= args.max_seconds or (args.cancel_file and Path(args.cancel_file).exists()):
                interrupted = True
                break
            row = training_row(encoded['train'], seed=args.seed, step=step, sampling=args.sampling)
            model.train()
            loss, grads = gradient(model, mx.array([row['tokens']]), row['answerStart'])
            optimizer.update(model, grads)
            mx.eval(model.parameters(), optimizer.state, loss)
            numeric = float(loss.item())
            if not math.isfinite(numeric):
                raise ValueError('Nonfinite training loss; experiment rejected')
            receipt['stepsCompleted'] += 1
            receipt['losses'].append(numeric)
            if receipt['stepsCompleted'] % 10 == 0:
                checkpoint()
                print(canonical({'step': receipt['stepsCompleted'], 'loss': numeric}), flush=True)
    except KeyboardInterrupt:
        interrupted = True
    after = evaluate()
    changed, changed_elements = [], 0
    for key, value in tree_flatten(model.trainable_parameters()):
        difference = mx.sum(value != initial[key])
        mx.eval(difference)
        number = int(difference.item())
        if number:
            changed.append(key); changed_elements += number
    receipt.update(status='trained-unaccepted' if receipt['stepsCompleted'] and changed else 'untrained',
        stoppedEarly=interrupted, finalValidationLoss=after,
        changedTensors=changed, changedParameterElements=changed_elements,
        validation=validation_gate(before, after), peakMemoryBytes=mx.get_peak_memory())
    checkpoint()
    print(canonical({key: receipt[key] for key in ('status', 'stepsCompleted', 'trainableParameters',
        'changedParameterElements', 'baselineValidationLoss', 'finalValidationLoss', 'elapsedSeconds')}), flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--steps', type=int, default=20)
    parser.add_argument('--max-seconds', type=int, default=600)
    parser.add_argument('--max-seq-length', type=int, default=256)
    parser.add_argument('--eval-examples', type=int, default=8)
    parser.add_argument('--learning-rate', type=float, default=1e-4)
    parser.add_argument('--seed', type=int, default=20261101)
    parser.add_argument('--sampling', choices=('random', 'shuffled'), default='random',
        help='random preserves legacy sampling; shuffled visits each complete example once per epoch')
    parser.add_argument('--cancel-file')
    parser.add_argument('--resume-adapter')
    args = parser.parse_args()
    if not 1 <= args.steps <= 2000 or not 30 <= args.max_seconds <= 3600 or not 64 <= args.max_seq_length <= 1024:
        parser.error('Bounded steps, seconds and sequence length are required')
    if not 2 <= args.eval_examples <= 32 or not 0 < args.learning_rate <= .001:
        parser.error('Invalid evaluation or learning-rate limit')
    run(args)


if __name__ == '__main__':
    main()
