"""Fact-bound supervision and lane isolation; these tests do not claim ML quality."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
import advertising_learning as learning
import advertising_promotion as promotion
import individual_copy
from learning_memory import LearningMemory
import learning_scheduler as scheduler
import learning_service
import local_text_model
import text_model_contract as contract

AD_CONFIG={'model_type':'qwen3','hidden_size':2560,'num_hidden_layers':36,
    'num_attention_heads':32,'num_key_value_heads':8,'head_dim':128,'vocab_size':151936,
    'tie_word_embeddings':True,'rope_theta':5_000_000,'max_position_embeddings':262144,
    'quantization':{'bits':4,'group_size':64}}
TRANSLATION_CONFIG={'model_type':'qwen2','hidden_size':896,'num_hidden_layers':24,
    'num_attention_heads':14,'num_key_value_heads':2}
FIELDS={'city':'Москва','price':'85000','deposit':'35000','area':'42',
    'floor':'2','floors':'12','layout':'one','features':['chairs','fridge','oven']}
REVIEW={'actor':'assistant_editorial_test','humanVerified':False,
    'reason':'Проверены цена, депозит, площадь, этаж, планировка и каждый предмет по исходным фактам.',
    'reviewedAt':'2026-01-01T00:00:00+00:00'}


def complete(brief):
    facts=brief['facts']
    return {'title':'莫斯科'+brief['layout']+'出租','body':'\n'.join(facts['核心参数'])+'\n'+
        facts['其他核实资料']+'\n'+facts['已知费用与限制']+'\n核实的室内物品包括：'+
        '、'.join(facts['核实物品清单'])+'。'}


class GroundedSupervisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief=individual_copy.build_brief(FIELDS)
        cls.target=complete(cls.brief)
        cls.payload=learning.prepare_payload(FIELDS,cls.target,review=REVIEW)

    def test_canonical_input_is_property_specific_and_bound_to_fields(self):
        other=learning.prepare_payload({**FIELDS,'price':'92000'},
            complete(individual_copy.build_brief({**FIELDS,'price':'92000'})),review=REVIEW)
        self.assertNotEqual(self.payload['inputSha256'],other['inputSha256'])
        self.assertIn('85000',json.dumps(self.payload['request'],ensure_ascii=False).replace(',',''))
        self.assertNotIn('Пример описания',json.dumps(self.payload['request'],ensure_ascii=False))
        stale=copy.deepcopy(self.payload); stale['fields']['price']='92000'
        with self.assertRaisesRegex(ValueError,'изменились'): learning.normalize_payload(stale)

    def test_assistant_review_cannot_masquerade_as_human_and_unknown_claims_fail(self):
        review={**REVIEW,'humanVerified':True}
        with self.assertRaisesRegex(ValueError,'проверку человека'):
            learning.prepare_payload(FIELDS,self.target,review=review)
        bad=copy.deepcopy(self.payload); bad['target']['body']+=' 配有洗碗机。'
        with self.assertRaisesRegex(ValueError,'сверку фактов'): learning.verify_target(bad)

    def test_pending_edit_requires_an_actual_source_review(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=LearningMemory(directory)
            pending=copy.deepcopy(self.payload); pending['review']=None
            event=memory.record('grounded_listing',pending,source='user-edit',group_id='property-one')
            with self.assertRaisesRegex(ValueError,'ещё не проверен'):
                memory.review(event['id'],'approved')
            self.assertEqual(memory.status(lane='advertising')['approvedExamples'],0)

    def test_old_approved_style_notes_do_not_unlock_advertising_training(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=LearningMemory(directory)
            for index in range(25):
                memory.record('description',{'text':'旧说明'+str(index),'language':'zh'},status='approved',group_id=str(index))
            memory.record('grounded_listing',self.payload,source='assistant-reviewed',status='approved',
                group_id='property-one',actor=REVIEW['actor'])
            status=memory.status(lane='advertising')
            self.assertEqual(status['approvedExamples'],1)
            self.assertEqual(status['styleOnlyExamples'],25)
            self.assertFalse(status['trainingEligible'])
            snap=memory.snapshot(lane='advertising')
            rows=[json.loads(line) for key in ('train','valid') for line in Path(snap[key]).read_text().splitlines()]
            self.assertEqual(len(rows),1); row=rows[0]
            self.assertEqual(row['messages'][:-1],self.payload['request']['messages'])
            self.assertEqual(row['provenance']['kind'],'assistant-reviewed')
            self.assertIs(row['provenance']['humanVerified'],False)

    def test_approved_duplicates_are_counted_by_supervision_not_review_timestamp(self):
        rows=[]
        for index in range(21):
            payload=copy.deepcopy(self.payload); payload['review']['reason']+=' '+str(index)
            rows.append({'kind':'grounded_listing','status':'approved','group_id':hashlib.sha256(str(index).encode()).hexdigest(),
                'fingerprint':str(index),'payload':json.dumps(payload)})
        summary=learning.lane_summary(rows)
        self.assertEqual(summary['approvedExamples'],1)
        self.assertEqual(summary['approvedRecords'],21)
        self.assertFalse(summary['trainingEligible'])

    def test_review_api_records_authenticated_actor_and_complete_grounded_target(self):
        with tempfile.TemporaryDirectory() as directory:
            response=learning_service.dispatch(directory,'learning',{'action':'record-grounded',
                'fields':FIELDS,'target':self.target,'groupId':'test-property','reviewed':True,
                'reason':REVIEW['reason']},actor='assistant_editorial_test')
            row=response[1]['event']
            self.assertEqual(row['kind'],'grounded_listing'); self.assertEqual(row['status'],'approved')
            self.assertIs(row['payload']['review']['humanVerified'],False)
            self.assertEqual(row['payload']['review']['actor'],'assistant_editorial_test')

    def test_consent_checks_exact_stored_grounded_text_without_restarting_fact_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            memory=LearningMemory(directory)
            event=memory.record('grounded_listing',self.payload,source='assistant-reviewed',status='approved',
                group_id='consent-property',actor=REVIEW['actor'])
            with memory._db() as db:
                stored=db.execute('SELECT * FROM examples WHERE id=?',(event['id'],)).fetchone()
                row=LearningMemory._training_row(stored)
            data={'version':1,'splits':{'train':[row],'validation':[]},
                'provenance':{'kind':'approved-local-memory'}}
            path=Path(directory)/'dataset.json'; path.write_text(json.dumps(data))
            with patch.object(learning,'verify_target',side_effect=AssertionError('No rendering while model is resident')):
                memory.register_dataset(path)
                memory.assert_dataset_consent(path)
                bad=copy.deepcopy(data); bad['splits']['train'][0]['messages'][-1]['content']+=' invented'
                path.write_text(json.dumps(bad))
                with self.assertRaisesRegex(ValueError,'Содержимое корпуса'):
                    memory.assert_dataset_consent(path)
                path.write_text(json.dumps(data))
                memory.withdraw(event['id'],actor='assistant_editorial_test',reason='Source proof withdrawn')
                with self.assertRaisesRegex(ValueError,'отозван'):
                    memory.assert_dataset_consent(path)
                copied=copy.deepcopy(data)
                copied['provenance']={}; copied['splits']['train'][0].update(id='renamed-copy',provenance={})
                path.write_text(json.dumps(copied))
                with self.assertRaisesRegex(ValueError,'копию отозванного'):
                    memory.assert_dataset_consent(path)


class AdvertisingLaneTests(unittest.TestCase):
    def test_native_batch_encoding_is_tokens_and_empty_template_thinking_is_not_a_label(self):
        class Tokenizer:
            eos_token='<|im_end|>'
            def encode(self,text,**unused): return [ord(char) for char in text]
            def apply_chat_template(self,messages,tokenize=True,add_generation_prompt=False,**unused):
                prefix='user:'+messages[0]['content']+'\nassistant\n'
                text=prefix if add_generation_prompt else prefix+'<think>\n\n</think>\n\n'+messages[-1]['content']+self.eos_token+'\n'
                return {'input_ids':self.encode(text),'attention_mask':[1]*len(text)} if tokenize else text
        row={'id':'one','groupId':'property','messages':[{'role':'user','content':'facts'},
            {'role':'assistant','content':'标题：房源\n正文：\n已核实的文字。'}]}
        tokenizer=Tokenizer()
        encoded=contract.encode_record(tokenizer,row,1000,chat_template_kwargs={'enable_thinking':False},
            target_format=contract.training_contract(AD_CONFIG,'advertising')['targetFormat'])
        text=''.join(chr(token) for token in encoded['tokens'])
        self.assertNotIn('<think>',text)
        self.assertTrue(text.endswith(row['messages'][-1]['content']+'<|im_end|>\n'))
        self.assertEqual(text[encoded['answerStart']:],row['messages'][-1]['content']+'<|im_end|>\n')
        self.assertIsNone(contract.encode_record(tokenizer,row,len(encoded['tokens'])-1,
            target_format=contract.training_contract(AD_CONFIG,'advertising')['targetFormat']))
        for bad in ({'attention_mask':[1,1]}, {'input_ids':[[1,2]]}, {'input_ids':['key','not-token']}):
            with self.subTest(shape=bad),self.assertRaises(ValueError): contract._token_ids(bad)

    def test_4b_advertising_has_a_separate_real_projection_count(self):
        profile=contract.training_contract(AD_CONFIG,'advertising')
        self.assertEqual(profile['trainableParameters'],4*4*((2560+4096)+(2560+1024)))
        self.assertEqual(profile['trainableParameters'],163840)
        self.assertEqual(profile['tensorCount'],16)
        self.assertEqual(contract.training_contract(TRANSLATION_CONFIG)['trainableParameters'],540672)
        with self.assertRaises(ValueError): contract.training_contract(AD_CONFIG,'translation')
        with self.assertRaises(ValueError): contract.training_contract(TRANSLATION_CONFIG,'advertising')

    def test_training_uses_the_same_non_thinking_template_and_never_cuts_targets(self):
        calls=[]
        class Tokenizer:
            def apply_chat_template(self,messages,**kwargs):
                calls.append(kwargs)
                return [1,2] if len(messages)==1 else [1,2,3,4]
        row={'id':'sample','groupId':'one','messages':[{'role':'user','content':'x'},{'role':'assistant','content':'y'}]}
        result=contract.encode_record(Tokenizer(),row,4,chat_template_kwargs={'enable_thinking':False})
        self.assertIsNotNone(result); self.assertTrue(all(call.get('enable_thinking') is False for call in calls))
        self.assertIsNone(contract.encode_record(Tokenizer(),row,3,chat_template_kwargs={'enable_thinking':False}))

    def test_runtime_sends_advertising_adapter_only_to_advertising_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); model=root/'model'; model.mkdir(); (model/'config.json').write_text(json.dumps(AD_CONFIG))
            python=root/'python'; python.write_text('fixture')
            (root/'text-ai.json').write_text(json.dumps({'enabled':True,'modelPath':str(model),'pythonPath':str(python),
                'advertisingModelPath':str(model),'advertisingPythonPath':str(python),
                'adapterPath':'translation-adapter','useCandidateAdapter':True,
                'advertisingAdapterPath':'advertising-adapter','useAdvertisingAdapter':True}))
            with patch.object(local_text_model.subprocess,'Popen',side_effect=OSError('fixture-no-process')) as spawn:
                local_text_model.generate({'messages':[{'role':'user','content':'facts'}],'purpose':'advertising'},directory)
                self.assertIn('advertising-adapter',spawn.call_args.args[0]); self.assertNotIn('translation-adapter',spawn.call_args.args[0])
                local_text_model.generate({'messages':[{'role':'user','content':'translate'}]},directory)
                self.assertIn('translation-adapter',spawn.call_args.args[0]); self.assertNotIn('advertising-adapter',spawn.call_args.args[0])

    def test_scheduler_does_not_resume_or_count_the_historical_translation_lane(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); model=root/'model'; model.mkdir(); (model/'config.json').write_text(json.dumps(AD_CONFIG))
            python=root/'python'; python.write_text('fixture')
            (root/'text-ai.json').write_text(json.dumps({'enabled':True,'advertisingModelPath':str(model),
                'advertisingPythonPath':str(python),'adapterPath':'historical','useCandidateAdapter':True}))
            memory=LearningMemory(directory)
            for i in range(25): memory.record('translation',{'ru':'Стол '+str(i),'zh':'桌子'+str(i)},status='approved',group_id=str(i))
            scheduler._write(root/'learning/scheduler-state.json',{'phase':'accepted','stepsCompleted':800,'jobId':'legacy'})
            scheduler.save_config(directory,{'enabled':True})
            trainer=scheduler.IdleTrainer(directory,clock=lambda:1000)
            trainer.started=0
            with patch.object(trainer,'_run') as run:
                result=trainer.tick(); run.assert_not_called()
            self.assertEqual(result['lane'],'advertising'); self.assertEqual(result['phase'],'collecting')
            self.assertEqual(result['approvedExamples'],0); self.assertNotEqual(result.get('stepsCompleted'),800)
            self.assertEqual(json.loads((root/'learning/scheduler-state.json').read_text())['stepsCompleted'],800)
            command,selected=trainer._runtime()
            self.assertEqual(selected,model); self.assertIn(str(python),command)
            self.assertEqual(json.loads((root/'text-ai.json').read_text())['adapterPath'],'historical')


if __name__=='__main__': unittest.main()
