import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import local_text_model as runtime
import text_model_contract as contract


def script(name):
    spec=importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/name)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def data():
    return {'version':1, 'splits':{split:[{'id':split+str(i),'groupId':split+str(i),
        'status':'approved','source':'user-edit','messages':[{'role':'user','content':split+str(i)},
            {'role':'assistant','content':'答复'+split+str(i)}]} for i in range(2)]
        for split in ('train','validation')}}


class ContractTests(unittest.TestCase):
    def test_parameter_count_uses_real_q_v_dimensions(self):
        cfg={'model_type':'qwen2','hidden_size':896,'num_hidden_layers':24,
            'num_attention_heads':14,'num_key_value_heads':2}
        self.assertEqual(contract.expected_parameters(cfg),540672)
        cfg['hidden_size']=1024
        with self.assertRaises(ValueError): contract.expected_parameters(cfg)

    def test_property_group_cannot_cross_validation(self):
        value=data(); value['splits']['validation'][0]['groupId']='train0'
        with self.assertRaisesRegex(ValueError,'Group leaks'): contract.validate_dataset(value)

    def test_generated_examples_are_not_automatically_approved(self):
        value=data(); value['splits']['train'][0]['status']='generated'
        with self.assertRaisesRegex(ValueError,'Unapproved'): contract.validate_dataset(value)

    def test_duplicate_text_cannot_make_validation_look_independent(self):
        value=data(); value['splits']['validation'][0]['messages']=copy.deepcopy(value['splits']['train'][0]['messages'])
        with self.assertRaisesRegex(ValueError,'Duplicate text'): contract.validate_dataset(value)

    def test_same_input_with_a_reworded_answer_is_still_leakage(self):
        value=data(); value['splits']['validation'][0]['messages'][0]['content']='train0'
        with self.assertRaisesRegex(ValueError,'same input leaks'): contract.validate_dataset(value)

    def test_token_loss_boundary_never_silently_truncates_answer(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return [1,2,3] if len(messages)==1 else [1,2,3,4,5,6]
        row=data()['splits']['train'][0]
        self.assertEqual(contract.encode_record(Tokenizer(),row,6)['answerStart'],3)
        self.assertIsNone(contract.encode_record(Tokenizer(),row,5))

    def test_unstable_chat_template_fails_before_training(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return [1,2,3] if len(messages)==1 else [9,2,3,4,5]
        with self.assertRaisesRegex(ValueError,'stable assistant'): contract.encode_record(Tokenizer(),data()['splits']['train'][0],10)

    def test_loss_improvement_is_not_publication_approval(self):
        result=contract.validation_gate(2,1)
        self.assertTrue(result['lossImproved']); self.assertFalse(result['autoPromote'])
        self.assertFalse(contract.validation_gate(float('nan'),1)['lossImproved'])

    def test_smoke_evaluation_keeps_hallucinations_and_missing_numbers_visible(self):
        evaluator=script('evaluate-local-text-model.py')
        probe=evaluator.PROBES[-1]
        result=evaluator.check(probe,'{"title":"莫斯科39㎡", "body":"沙发、书桌、免费冰箱。"}')
        self.assertTrue(result['jsonShapeCorrect'])
        self.assertFalse(result['numbersPreserved'])
        self.assertEqual(result['unsupportedCommonClaims'],['冰箱','免费'])

    def test_seed_excludes_unreviewed_open_dictionary_and_groups_languages(self):
        seed=script('prepare-local-text-training.py').prepare(ROOT)
        self.assertEqual(sum(map(len,seed['splits'].values())),132)
        groups={}
        for split, rows in seed['splits'].items():
            for row in rows:
                self.assertEqual(row['status'],'project-authored')
                self.assertNotIn('ru-zh-open-lexicon',row['source'])
                self.assertEqual(groups.setdefault(row['groupId'],split),split)

    def test_balanced_seed_keeps_both_translation_directions_and_json_families_together(self):
        module=script('prepare-balanced-text-training.py')
        seed=module.prepare(); families={}
        self.assertEqual(sum(map(len,seed['splits'].values())),108)
        for split,rows in seed['splits'].items():
            for row in rows:
                self.assertTrue(row['fictional'])
                self.assertNotIn('74000',json.dumps(row))
                self.assertEqual(families.setdefault(row['groupId'],split),split)
                if row['task']=='listing':
                    answer=json.loads(row['messages'][-1]['content'])
                    self.assertEqual(set(answer),{'title','body'})
        self.assertEqual(len(families),36)


class RuntimeTests(unittest.TestCase):
    def test_requests_bound_context_tokens_and_temperature(self):
        result=runtime.clean_request({'messages':[{'role':'user','content':'Холодильник'}]})
        self.assertEqual(result['maxTokens'],350)
        for update in ({'maxTokens':9999},{'temperature':float('nan')},{'temperature':True},
                {'messages':[{'role':'tool','content':'bad'}]}):
            with self.assertRaises(ValueError): runtime.clean_request({**result,**update})

    def test_disabled_model_returns_without_importing_mlx_or_spawning(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime.subprocess,'Popen') as process:
            result=runtime.generate({'messages':[{'role':'user','content':'Холодильник'}]},folder)
            self.assertEqual(result['status'],'unavailable'); process.assert_not_called()

    def test_resume_requires_matching_snapshot_and_atomic_hashes(self):
        trainer=script('train-local-text-model.py')
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); out=base/'out'; out.mkdir(); model=base/'model'; model.mkdir()
            dataset=base/'data.json'; dataset.write_text('{}')
            (model/'config.json').write_text(json.dumps({'model_type':'qwen2','hidden_size':896,
                'num_hidden_layers':24,'num_attention_heads':14,'num_key_value_heads':2}))
            for name in ('adapters.safetensors','optimizer.safetensors','initial.safetensors'):
                (out/name).write_bytes(name.encode())
            args=SimpleNamespace(resume_adapter=str(out/'adapters.safetensors'),dataset=str(dataset),
                seed=1,max_seq_length=256,learning_rate=.0001)
            receipt={'datasetSha256':contract.file_sha(dataset),'baseConfigSha256':contract.file_sha(model/'config.json'),
                'trainableParameters':contract.EXPECTED_TRAINABLE,'seed':1,'lora':contract.LORA,
                'limits':{'maxSequenceLength':256},'learningRate':.0001,
                **{key:contract.file_sha(out/name) for name,key in [('adapters.safetensors','adapterSha256'),
                    ('optimizer.safetensors','optimizerSha256'),('initial.safetensors','initialSha256')]}}
            (out/'trainreceipt.json').write_text(json.dumps(receipt))
            self.assertEqual(trainer.resume_receipt(out,args,model),receipt)
            (out/'optimizer.safetensors').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'hashes'): trainer.resume_receipt(out,args,model)


if __name__=='__main__': unittest.main()
