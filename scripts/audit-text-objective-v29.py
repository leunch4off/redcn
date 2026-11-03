#!/usr/bin/env python3
"""Offline token/target audit using the installed native inference tokenizer."""
import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from text_model_contract import answer_loss_mask, encode_record, file_sha, load_dataset


def audit(tokenizer, data, maximum):
    eos = getattr(tokenizer, 'eos_token_ids', None)
    if not eos:
        eos = [getattr(tokenizer, 'eos_token_id', None)]
    eos = {x for x in eos if type(x) is int}
    if not eos:
        raise ValueError('Tokenizer must expose the real EOS token IDs')
    summary = {}
    for split, rows in data['splits'].items():
        lengths, answers, suffix, eos_counts = [], [], [], []
        for row in rows:
            encoded = encode_record(tokenizer, row, maximum)
            if encoded is None:
                raise ValueError('A complete supervised example exceeds the limit: '+row['id'])
            tokens, start = encoded['tokens'], encoded['answerStart']
            prompt = tokenizer.apply_chat_template(row['messages'][:-1], tokenize=False, add_generation_prompt=True)
            if list(tokenizer.encode(prompt)) != tokens[:start]:
                raise ValueError('Inference prompt tokens differ from the training prefix: '+row['id'])
            mask = answer_loss_mask(len(tokens), start)
            trained_positions = [i+1 for i, enabled in enumerate(mask) if enabled]
            if trained_positions != list(range(start, len(tokens))):
                raise ValueError('Prompt or answer token has an incorrect loss mask')
            ends = [position for position in trained_positions if tokens[position] in eos]
            if len(ends) != 1:
                raise ValueError('Assistant target needs exactly one EOS: '+row['id'])
            # A template may retain a newline after EOS. It is measured and
            # disclosed, not silently treated as another assistant response.
            after = tokens[ends[0]+1:]
            tail = tokenizer.decode(after)
            if tail.strip():
                raise ValueError('Non-whitespace content follows the assistant EOS')
            lengths.append(len(tokens)); answers.append(sum(mask)); suffix.append(len(after)); eos_counts.append(len(ends))
        summary[split] = {'examples': len(rows), 'minTokens': min(lengths), 'maxTokens': max(lengths),
            'maxSupervisedTargets': max(answers), 'maxPostEosWhitespaceTokens': max(suffix),
            'allPrefixesMatchInference': True, 'allMasksExcludePrompt': True,
            'eosSupervisedExactlyOnce': all(n==1 for n in eos_counts), 'skipped': 0}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-seq-length', type=int, default=1024)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Run this audit in the installed native ARM64 environment')
    if not 64 <= args.max_seq_length <= 1024:
        raise ValueError('Bounded sequence length required')
    target = Path(args.output)
    if target.exists() or target.is_symlink():
        raise ValueError('Use a new audit output')
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from mlx_lm.utils import load_tokenizer
    tokenizer = load_tokenizer(Path(args.model))
    start = time.monotonic()
    result = {'version': 1, 'datasetSha256': file_sha(args.dataset),
        'modelConfigSha256': file_sha(Path(args.model)/'config.json'),
        'objective': 'assistant-targets-including-template-terminator-and-suffix-v1',
        'splits': audit(tokenizer, load_dataset(args.dataset), args.max_seq_length),
        'nativeArm64': True, 'noModelWeightsLoaded': True, 'elapsedSeconds': time.monotonic()-start}
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
