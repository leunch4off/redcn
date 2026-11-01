import importlib.util
import io
import json
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('text_training_cli', ROOT/'scripts/train-local-text-model.py')
trainer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trainer)
import learning_scheduler as scheduler


class TrainingCliTests(unittest.TestCase):
    def arguments(self, length):
        return ['train-local-text-model.py', '--model', '/unused/model',
            '--dataset', '/unused/dataset.json', '--output', '/unused/output',
            '--max-seq-length', str(length)]

    def test_explicit_1024_token_budget_reaches_training(self):
        with patch.object(sys, 'argv', self.arguments(1024)), patch.object(trainer, 'run') as run:
            trainer.main()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0].max_seq_length, 1024)
        self.assertEqual(run.call_args.args[0].sampling, 'random')

    def test_shuffled_sampling_is_an_explicit_cli_option(self):
        with patch.object(sys, 'argv', self.arguments(1024)+['--sampling', 'shuffled']), \
                patch.object(trainer, 'run') as run:
            trainer.main()
        self.assertEqual(run.call_args.args[0].sampling, 'shuffled')

    def test_oversized_token_budget_is_rejected_before_training(self):
        with patch.object(sys, 'argv', self.arguments(1025)), patch.object(trainer, 'run') as run, \
                patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit) as stopped:
            trainer.main()
        self.assertEqual(stopped.exception.code, 2)
        run.assert_not_called()

    def test_scheduler_keeps_its_smaller_token_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            accepted = scheduler.save_config(directory, {'maxSequenceLength': 512})
            self.assertEqual(accepted['maxSequenceLength'], 512)
            for length in (513, 1024):
                with self.subTest(length=length), self.assertRaises(ValueError):
                    scheduler.save_config(directory, {'maxSequenceLength': length})
            self.assertEqual(scheduler.load_config(directory)['maxSequenceLength'], 512)


class SamplingTests(unittest.TestCase):
    def test_shuffled_sampling_covers_every_example_once_per_epoch(self):
        rows = [{'id': str(index)} for index in range(7)]
        epochs = [[trainer.training_row(rows, seed=37, step=step, sampling='shuffled')['id']
            for step in range(epoch*len(rows), (epoch+1)*len(rows))] for epoch in range(3)]
        for order in epochs:
            self.assertCountEqual(order, [row['id'] for row in rows])
        self.assertNotEqual(epochs[0], epochs[1])

    def test_resume_uses_the_exact_remaining_step_order(self):
        # Known order spans three epochs, with checkpoints both inside an epoch
        # and on its boundary. A resumed process needs no prior random state.
        expected = [3, 1, 2, 6, 0, 4, 5, 2, 1, 4, 0, 6, 3, 5, 6, 5, 4, 0, 3, 2, 1]
        for checkpoint in (0, 5, 7, 11, 14):
            with self.subTest(checkpoint=checkpoint):
                resumed = [trainer.training_row(list(range(7)), seed=37, step=step,
                    sampling='shuffled') for step in range(checkpoint, len(expected))]
                self.assertEqual(resumed, expected[checkpoint:])

    def test_default_random_sampling_preserves_legacy_order(self):
        rows = list(range(11))
        for step in range(30):
            self.assertEqual(trainer.training_row(rows, seed=37, step=step),
                random.Random(37+step).choice(rows))

    def test_checkpoint_sampling_is_part_of_resume_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            model = base/'model'; model.mkdir()
            output = base/'output'; output.mkdir()
            dataset = base/'data.json'; dataset.write_text('{}')
            (model/'config.json').write_text('{}')
            files = [('adapters.safetensors', 'adapterSha256'),
                ('optimizer.safetensors', 'optimizerSha256'), ('initial.safetensors', 'initialSha256')]
            for filename, _ in files:
                (output/filename).write_bytes(filename.encode())
            args = SimpleNamespace(resume_adapter=str(output/'adapters.safetensors'),
                dataset=str(dataset), seed=37, max_seq_length=1024, learning_rate=.00001,
                sampling='random')
            receipt = {'datasetSha256': trainer.file_sha(dataset),
                'baseConfigSha256': trainer.file_sha(model/'config.json'),
                'trainableParameters': trainer.EXPECTED_TRAINABLE, 'seed': 37, 'lora': trainer.LORA,
                'limits': {'maxSequenceLength': 1024}, 'learningRate': .00001,
                **{key: trainer.file_sha(output/filename) for filename, key in files}}
            path = output/'trainreceipt.json'
            path.write_text(json.dumps(receipt))
            # Receipts created before the flag existed identify legacy random.
            self.assertEqual(trainer.resume_receipt(output, args, model), receipt)
            args.sampling = 'shuffled'
            with self.assertRaisesRegex(ValueError, 'sampling mode'):
                trainer.resume_receipt(output, args, model)
            receipt['sampling'] = 'shuffled'
            path.write_text(json.dumps(receipt))
            self.assertEqual(trainer.resume_receipt(output, args, model), receipt)
            args.sampling = 'random'
            with self.assertRaisesRegex(ValueError, 'sampling mode'):
                trainer.resume_receipt(output, args, model)


if __name__ == '__main__':
    unittest.main()
