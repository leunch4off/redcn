import copy
import builtins
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'cli'))
import local_content
from advertising_structure import (PLAN_COUNT, TokenChoiceConstraint, fingerprint,
    parse_plan, plan_json, render_plan, request_for_structure)
from text_model_contract import answer_loss_mask, content_sha, file_sha, load_dataset, validate_dataset
from text_corpus_audit import audit_corpora
from text_evaluation_audit import validator_audit, summarize_direct_report
from text_native_evaluation import measure_stream
from rental_heldout_quality import load_cases, check_training_disjoint


def script(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/name)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class ObjectiveAndCorpusTests(unittest.TestCase):
    def test_reload_loss_measurement_stays_numeric_and_rejects_nonfinite_results(self):
        compare=script('check-text-reload-v29.py').compare_reload_loss
        result=compare(1.25,[1.0,1.5])
        self.assertEqual(result['reloadedValidationLoss'],1.25)
        self.assertTrue(result['lossReproduced'])
        self.assertFalse(compare(1.2,[1.0,1.5])['lossReproduced'])
        nonfinite=compare(1.25,[float('nan')])
        self.assertFalse(nonfinite['lossReproduced'])
        self.assertIsNone(nonfinite['reloadedValidationLoss'])
        with self.assertRaises(ValueError):compare(1.25,[])

    def consent_fixture(self, folder):
        from learning_memory import LearningMemory
        memory=LearningMemory(folder)
        events=[memory.record('translation',{'ru':f'Площадь квартиры {i}: {31+i} м².',
                    'zh':f'公寓{i}，面积{31+i}平方米。'},source='manual',status='approved',
                    group_id=f'fixture-property-{i}') for i in range(4)]
        with memory._db() as db:
            rows=[{**memory._training_row(db.execute('SELECT * FROM examples WHERE id=?',
                (event['id'],)).fetchone()),'status':'approved'} for event in events]
        data={'version':1,'provenance':{'kind':'approved-local-memory'},
              'splits':{'train':rows[:2],'validation':rows[2:]}}
        path=Path(folder)/'dataset.json';path.write_text(json.dumps(data,ensure_ascii=False))
        return memory,events,path

    def test_changed_source_cannot_replace_loaded_training_consent(self):
        trainer=script('train-local-text-model.py')
        with tempfile.TemporaryDirectory() as folder:
            memory,events,path=self.consent_fixture(folder)
            frozen=trainer.FrozenTrainingDataset(path)
            args=SimpleNamespace(dataset=str(path),consent_cache=folder)
            consent=trainer.prepare_consent(args,frozen.data,snapshot=frozen)
            memory.withdraw(events[0]['id'])
            replacement=(ROOT/'data/learning/text-v29-advertising-only.json').read_bytes()
            path.write_bytes(replacement)
            self.assertTrue(memory.assert_dataset_consent(path))
            with self.assertRaises(trainer.DatasetChanged):frozen.assert_consent(consent)
            self.assertEqual(frozen.data['splits']['train'][0]['id'],events[0]['id'])
            self.assertNotEqual(frozen.sha256,file_sha(path))
            # A non-local authored corpus has the same immutable source guard.
            authored=trainer.FrozenTrainingDataset(path);path.write_bytes(frozen.raw)
            with self.assertRaises(trainer.DatasetChanged):authored.assert_consent(None)
            with self.assertRaises(trainer.DatasetChanged):
                trainer.prepare_consent(args,authored.data,snapshot=authored)

    def test_replacement_between_hash_and_consent_still_checks_frozen_rows(self):
        trainer=script('train-local-text-model.py')
        with tempfile.TemporaryDirectory() as folder:
            memory,events,path=self.consent_fixture(folder)
            frozen=trainer.FrozenTrainingDataset(path)
            memory.withdraw(events[0]['id'])
            actual_check=memory.assert_dataset_consent
            def replace_then_check(path,**kwargs):
                Path(path).write_bytes((ROOT/'data/learning/text-v29-advertising-only.json').read_bytes())
                self.assertIs(kwargs['snapshot'],frozen.raw)
                return actual_check(path,**kwargs)
            with patch.object(memory,'assert_dataset_consent',side_effect=replace_then_check):
                with self.assertRaisesRegex(ValueError,'отозван'):
                    frozen.assert_consent(memory)

    def test_training_rejects_changed_snapshot_before_importing_mlx_and_releases_candidate_lease(self):
        trainer=script('train-local-text-model.py')
        from training_job_state import activity
        with tempfile.TemporaryDirectory() as folder:
            memory,events,path=self.consent_fixture(folder)
            model=Path(folder)/'model';model.mkdir()
            (model/'config.json').write_text(json.dumps({'model_type':'qwen2','hidden_size':896,
                'num_hidden_layers':24,'num_attention_heads':14,'num_key_value_heads':2}))
            output=Path(folder)/'candidate'
            args=SimpleNamespace(dataset=str(path),consent_cache=folder,model=str(model),
                output=str(output),max_seq_length=256)
            prepare=trainer.prepare_consent
            def swap_after_preparation(*args,**kwargs):
                result=prepare(*args,**kwargs)
                path.write_bytes((ROOT/'data/learning/text-v29-advertising-only.json').read_bytes())
                return result
            original_import=builtins.__import__
            def forbid_mlx(name,*args,**kwargs):
                if name=='mlx' or name.startswith(('mlx.','mlx_lm')):
                    self.fail('MLX imported before detecting changed training data')
                return original_import(name,*args,**kwargs)
            with patch.object(trainer.platform,'system',return_value='Darwin'), \
                    patch.object(trainer.platform,'machine',return_value='arm64'), \
                    patch.object(trainer,'prepare_consent',side_effect=swap_after_preparation), \
                    patch.object(builtins,'__import__',side_effect=forbid_mlx):
                with self.assertRaises(trainer.DatasetChanged):trainer.run(args)
            self.assertFalse(activity(output)['active'])
            self.assertFalse((output/'trainreceipt.json').exists())

    def test_snapshot_registration_binds_original_sha_and_invalid_snapshot_never_falls_back(self):
        trainer=script('train-local-text-model.py')
        with tempfile.TemporaryDirectory() as folder:
            memory,events,path=self.consent_fixture(folder)
            frozen=trainer.FrozenTrainingDataset(path)
            path.write_bytes((ROOT/'data/learning/text-v29-advertising-only.json').read_bytes())
            digest=memory.register_dataset(path,snapshot=frozen.raw)
            self.assertEqual(digest,frozen.sha256)
            self.assertIn(frozen.sha256,memory.provenance(events[0]['id'])['datasetHashes'])
            for invalid in ('{}',bytearray(b'{}'),b'',b'x'*8_000_001):
                with self.subTest(type=type(invalid).__name__,length=len(invalid)):
                    with self.assertRaises(ValueError):memory.assert_dataset_consent(path,snapshot=invalid)

    def test_loss_masks_first_answer_and_eos_without_prompt(self):
        self.assertEqual(answer_loss_mask(7,3),[False,False,True,True,True,True])
        for n,start in [(7,0),(7,7),(True,1),(7,True)]:
            with self.assertRaises(ValueError):answer_loss_mask(n,start)

    def test_native_audit_contract_checks_real_prefix_eos_and_suffix(self):
        class Tokenizer:
            eos_token_ids={999}
            def apply_chat_template(self,messages,tokenize=True,**kw):
                return ([1,2,3] if len(messages)==1 else [1,2,3,40,41,999,10]) if tokenize else 'prompt'
            def encode(self,text):return [1,2,3]
            def decode(self,tokens):return '\n' if tokens==[10] else ''
        row={'id':'a','groupId':'a','messages':[{'role':'user','content':'问'},{'role':'assistant','content':'答'}]}
        result=script('audit-text-objective-v29.py').audit(Tokenizer(),{'splits':{'train':[row],'validation':[row]}},64)
        self.assertEqual(result['train']['maxSupervisedTargets'],4)
        self.assertEqual(result['train']['maxPostEosWhitespaceTokens'],1)
        self.assertTrue(result['train']['eosSupervisedExactlyOnce'])

    def test_frozen_mixtures_holdout_and_protocol_match(self):
        protocol=json.loads((ROOT/'data/quality/text-experiment-v29.json').read_text())
        for path,digest in protocol['artifacts'].items():self.assertEqual(file_sha(ROOT/path),digest,path)
        heldout=load_cases(ROOT/'data/quality/rental-heldout-v29.json')
        self.assertEqual(len(heldout['cases']),30)
        self.assertEqual(len({row['groupId'] for row in heldout['cases']}),30)
        for name,amount in [('advertising-only',360),('translation-retention',600)]:
            data=load_dataset(ROOT/('data/learning/text-v29-'+name+'.json'))
            self.assertEqual(len(data['splits']['train']),amount)
            self.assertEqual(len(data['splits']['validation']),120)
            self.assertFalse(check_training_disjoint(heldout,data)['exactTextOverlap'])
        self.assertFalse(protocol['counts']['advertising-only']['train']['tasks']['translation-ru-zh'])

    def test_direct_cli_requires_consent_for_approved_or_laundered_operator_rows(self):
        trainer=script('train-local-text-model.py')
        for row in ({'status':'approved'},{'status':'project-authored','provenance':{'kind':'operator-reviewed'}}):
            data={'splits':{'train':[row],'validation':[]}}
            self.assertTrue(trainer.requires_consent_cache(data))
            with self.assertRaisesRegex(ValueError,'consent-cache'):
                trainer.prepare_consent(SimpleNamespace(dataset='unused',consent_cache=None),data)
        authored=load_dataset(ROOT/'data/learning/text-v29-translation-retention.json')
        self.assertFalse(trainer.requires_consent_cache(authored))

    def test_global_audit_finds_history_leak_and_missing_permission(self):
        def row(identity,text):
            return {'id':identity,'groupId':identity,'source':'fixture','status':'project-authored',
                'messages':[{'role':'user','content':json.dumps({'verifiedText':text})},{'role':'assistant','content':text+' 回复'}]}
        a='Независимая карточка, площадь47метров, станцияГранит, аренда69123рубля.'
        b='另一套独立资料，面积三十九平方米，车站北方，租金为每月六万九千卢布。'
        data={'version':1,'splits':{'train':[row('a',a),row('b',b)],
            'validation':[row('c','Проверочная квартира со своей площадью и суммой оплаты, станцияЛес.'),
                          row('d','Новый отдельный источник с другими условиями и подробным описанием.') ]}}
        data['splits']['validation'][0]['messages'][0]['content']=json.dumps({'verifiedText':'Different independent facts','previousTextToAvoid':a})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'corpus.json';path.write_text(json.dumps(validate_dataset(data)))
            result=audit_corpora([path])
        self.assertFalse(result['ready'])
        self.assertTrue(result['exactLeaks'])
        self.assertEqual(result['missingProvenance']['explicitPermission'],4)
        self.assertNotIn(a,json.dumps(result,ensure_ascii=False))


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.baseline={'titles':['莫斯科租房资料'],
            'body':'莫斯科。月租85000卢布。\n\n面积40㎡。\n\n未配备洗碗机。宠物条件待确认。',
            'tags':'#莫斯科租房'}
        self.fields={'price':'85000','area':'40','priceCurrency':'RUB'}

    def test_finite_json_rejects_repair_extra_keys_and_unknown_plans(self):
        self.assertEqual(parse_plan('{"plan":12}',[12]),12)
        for value in ('{"plan":12','{"plan":12,"plan":13}','{"plan":true}','{"plan":13}','```json\n{"plan":12}\n```'):
            with self.assertRaises(ValueError):parse_plan(value,[12])
        with self.assertRaises(ValueError):request_for_structure('facts',[True],seed=1)

    def test_trie_permits_only_choices_and_then_eos(self):
        class Tokenizer:
            eos_token_ids={999}
            def encode(self,text,**kwargs):return list(text.encode())
            def decode(self,tokens):return bytes(tokens).decode()
        trie=TokenChoiceConstraint(Tokenizer(),[1,12],[1001,1002])
        for plan in (1,12):
            prefix=[1001,1002]
            for token in plan_json(plan).encode():
                self.assertIn(token,trie.allowed_tokens(prefix));prefix.append(token)
            self.assertEqual(trie.allowed_tokens(prefix),[999])
        with self.assertRaises(ValueError):trie.allowed_tokens([1001,1002,ord('x')])
        with self.assertRaises(ValueError):trie.allowed_tokens([7])

    def test_native_callback_after_prefill_uses_exact_anchor_and_eos_lookahead(self):
        # Explicit API-shape fixture for installed mlx-lm 0.30.6. This is not
        # native quality evidence: the real ARM64 smoke must also succeed.
        class Tokenizer:
            eos_token_ids={999}
            def encode(self,text,**kwargs):return list(text.encode())
            def decode(self,tokens):return bytes(tokens).decode()
        class Vector:
            def __init__(self,values):self.values=list(values)
            def tolist(self):return self.values[:]
            def __eq__(self,other):return Vector(value==other for value in self.values)
            def __or__(self,other):return Vector(a or b for a,b in zip(self.values,other.values))
        logits=SimpleNamespace(shape=(1,1100),values=[0.0]*1100)
        core=SimpleNamespace(arange=lambda count:Vector(range(count)),
            where=lambda mask,values,other:[value if keep else other for value,keep in zip(values.values,mask.values)])
        for initial in ([1001,1002],[1002]):
            with self.subTest(initial=initial), patch.dict(sys.modules,{'mlx':SimpleNamespace(core=core),'mlx.core':core}):
                trie=TokenChoiceConstraint(Tokenizer(),[1,12],[1001,1002])
                prefix=initial[:]
                for token in plan_json(12).encode():
                    masked=trie(Vector(prefix),logits)
                    self.assertEqual(masked[token],0.0)
                    self.assertEqual(masked[ord('x')],-float('inf'))
                    prefix.append(token)
                masked=trie(Vector(prefix),logits)
                self.assertEqual([i for i,value in enumerate(masked) if value==0],[999])
                prefix.append(999)
                # generate_step calls _step(EOS) before stream_generate sees
                # the yielded EOS and stops. This sole lookahead is EOS-only.
                masked=trie(Vector(prefix),logits)
                self.assertEqual([i for i,value in enumerate(masked) if value==0],[999])
                with self.assertRaises(ValueError):trie(Vector(prefix+[999]),logits)
        for initial in ([7],[1001],[1001,7],[True],[1002,123]):
            with self.subTest(invalid_initial=initial):
                trie=TokenChoiceConstraint(Tokenizer(),[12],[1001,1002])
                with self.assertRaises(ValueError):trie.callback_allowed_tokens(initial)
        for invalid_next in ([1002],[1001,1002,123],[1002,ord('x')],[1002,999]):
            with self.subTest(invalid_next=invalid_next):
                trie=TokenChoiceConstraint(Tokenizer(),[12],[1001,1002])
                trie.callback_allowed_tokens([1002])
                with self.assertRaises(ValueError):trie.callback_allowed_tokens(invalid_next)

    def test_all_plans_preserve_exact_fact_blocks_and_normalized_novelty(self):
        rows=[render_plan(self.baseline,i) for i in range(PLAN_COUNT)]
        self.assertEqual(len({fingerprint(row['title']) for row in rows}),PLAN_COUNT)
        self.assertEqual(len({fingerprint(row['body']) for row in rows}),PLAN_COUNT)
        for row in rows:
            self.assertTrue(row['provenance']['allFactBlocksCopiedExactly'])
            self.assertEqual(row['provenance']['modelAuthoredFactClaims'],0)
            for block in self.baseline['body'].split('\n\n'):self.assertIn(block,row['body'])
            self.assertEqual(local_content._advertising_issues(row,self.baseline,self.fields),[])

    def test_real_preview_preserves_provenance_history_and_exhaustion(self):
        def fake(request,*args,**kwargs):
            return {'status':'review','text':plan_json(request['allowedStructurePlans'][0]),'model':'fixture-choice'}
        with tempfile.TemporaryDirectory() as folder, patch.object(local_content,'advertising_baseline',return_value=(self.fields,self.baseline)), \
                patch('language_service._profile',return_value={'rules':{}}),patch.object(local_content,'_model',side_effect=fake):
            data={'styleMode':'advertising-structure','fields':self.fields,'groupId':'independent-fixture'}
            rows=[local_content.generate_preview(folder,data) for _ in range(PLAN_COUNT)]
            self.assertTrue(all(row['status']=='review' for row in rows),rows[-1])
            self.assertTrue(all(row['factualTextSource']=='verified-renderer' for row in rows))
            self.assertEqual(len({fingerprint(row['body']) for row in rows}),PLAN_COUNT)
            exhausted=local_content.generate_preview(folder,data)
            self.assertEqual(exhausted['status'],'blocked');self.assertTrue(exhausted['variantsExhausted'])

    def test_broken_model_choice_does_not_return_repaired_or_replaced_text(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(local_content,'advertising_baseline',return_value=(self.fields,self.baseline)), \
                patch('language_service._profile',return_value={'rules':{}}),patch.object(local_content,'_model',return_value={'status':'review','text':'{"plan":'}):
            result=local_content.generate_preview(folder,{'styleMode':'advertising-structure','fields':self.fields,'groupId':'broken'})
        self.assertEqual(result['status'],'blocked');self.assertNotIn('body',result)


class EvaluationAuditTests(unittest.TestCase):
    def test_native_measurement_preserves_partial_answers_and_real_tokens(self):
        def broken():
            yield SimpleNamespace(text='partial',generation_tokens=3,generation_tps=12)
            raise RuntimeError('Metal out of memory')
        measured=measure_stream(broken,maximum_tokens=10,probe_seconds=10)
        self.assertEqual(measured['text'],'partial')
        self.assertEqual(measured['generationTokens'],3)
        self.assertEqual(measured['stopReason'],'memory-error')
        self.assertEqual(measured['status'],'incomplete')

    def test_native_measurement_never_invents_eos_or_counts_chunks_as_tokens(self):
        measured=measure_stream(lambda:iter([SimpleNamespace(text='answer')]),maximum_tokens=10,probe_seconds=10)
        self.assertIsNone(measured['generationTokens'])
        self.assertEqual(measured['stopReason'],'missing-runtime-metrics')
        measured=measure_stream(lambda:iter([SimpleNamespace(text='answer',generation_tokens=2)]),
            maximum_tokens=10,probe_seconds=10)
        self.assertEqual(measured['stopReason'],'stream-ended')
        self.assertEqual(measured['status'],'measured')
        measured=measure_stream(lambda:iter([SimpleNamespace(text='answer',generation_tokens=10)]),
            maximum_tokens=10,probe_seconds=10)
        self.assertEqual(measured['stopReason'],'token-limit')

    def test_structure_benchmark_keeps_all_heldout_fact_blocks(self):
        benchmark=script('evaluate-advertising-structure.py')
        from advertising_evaluation import prepare_probes
        corpus=load_cases(ROOT/'data/quality/rental-heldout-v29.json')
        prepared=prepare_probes(corpus,rules={})
        for probe in prepared:
            if probe['task']=='listing' and probe['variant']==1:
                rendered=render_plan(probe['baseline'],0)
                self.assertTrue(benchmark.assess_rendered(probe,rendered)['passed'],probe['caseId'])

    def test_explicit_validator_cases_have_no_false_accepts_or_rejects(self):
        result=validator_audit(ROOT/'data/quality/text-validator-v29.json')
        self.assertEqual(result['cases'],36)
        self.assertEqual(result['confusion'],{'correctAccept':14,'correctReject':22})
        self.assertFalse(result['humanOrNativeEditorApproved'])

    def test_token_stop_and_memory_failure_are_separate(self):
        rows=[{'status':'incomplete','stopReason':reason,'checks':{'passed':False,'issues':[]}}
              for reason in ('token-limit','memory-error')]
        result=summarize_direct_report({'current':rows,'candidate':[]})['models']['current']
        self.assertEqual(result['stopReasons'],{'token-limit':1,'memory-error':1})
        self.assertEqual(result['completed'],0)


if __name__=='__main__':unittest.main()
