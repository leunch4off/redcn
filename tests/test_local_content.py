import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_content as content
from learning_memory import LearningMemory
import language_service
import local_server
import access_roles


class GroundedContent(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_draft_uses_only_relevant_tags_and_does_not_send_raw_source_photos_or_commission(self):
        baseline={'titles':['莫斯科｜40㎡｜8万卢布'],'body':'40㎡，月租8万卢布。配有沙发和书桌。','tags':'#莫斯科租房 #书桌'}
        memory=LearningMemory(self.directory)
        memory.record('tag_stat',{'tag':'书桌','views':2000},status='approved')
        memory.record('tag_stat',{'tag':'无关','views':9000000},status='approved')
        fields={'price':'80000','area':'40','features':['sofa','desk'],'source':'private raw html', 'photos':['privatephoto'],'commission':100}
        generated={'status':'review','text':json.dumps({'title':'莫斯科｜8万卢布','body':'40㎡，月租8万卢布。配有沙发和书桌。'},ensure_ascii=False),'model':'fixture'}
        with patch.object(language_service,'_profile',return_value={}),patch.object(language_service,'node_language',return_value=baseline) as render,patch.object(content,'_model',return_value=generated) as model:
            value=content.generate_preview(self.directory,{'fields':fields})
        self.assertEqual(value['status'],'review');self.assertEqual(value['tags'],'#书桌 #莫斯科租房')
        self.assertFalse(value['autoApply']);self.assertNotIn('source',render.call_args.args[0]['fields'])
        prompt=json.dumps(model.call_args.args[0],ensure_ascii=False)
        self.assertNotIn('privatephoto',prompt);self.assertNotIn('private raw',prompt)
        self.assertEqual(LearningMemory(self.directory).status()['counts']['pending'],2)
        with patch.object(language_service,'_profile',return_value={}),patch.object(language_service,'node_language',return_value=baseline),patch.object(content,'_model',return_value=generated):
            repeated=content.generate_preview(self.directory,{'fields':fields})
        self.assertEqual(repeated['status'],'blocked');self.assertIn('повторяет',' '.join(repeated['warnings']))
    def test_title_history_blocks_repeat_when_body_changes_and_cannot_train_itself(self):
        baseline={'titles':['莫斯科40㎡'],'body':'月租80000卢布，配有沙发。','tags':'#莫斯科租房'}
        fields={'price':'80000','area':'40','features':['sofa']}
        first={'title':'莫斯科公寓','body':'40㎡，月租80000卢布，配有沙发。'}
        second={'title':'🏡 莫斯科＿公寓！','body':'沙发已配备，公寓40㎡，月租80000卢布。'}
        def response(draft):return {'status':'review','text':json.dumps(draft,ensure_ascii=False),'model':'fixture'}
        with patch.object(language_service,'_profile',return_value={}),patch.object(language_service,'node_language',return_value=baseline):
            with patch.object(content,'_model',return_value=response(first)):
                self.assertEqual(content.generate_preview(self.directory,{'fields':fields,'groupId':'same-flat'})['status'],'review')
            with patch.object(content,'_model',return_value=response(second)) as model:
                result=content.generate_preview(self.directory,{'fields':fields,'groupId':'same-flat'})
            self.assertEqual(result['status'],'blocked');self.assertFalse(result['autoApply'])
            self.assertTrue(any('Заголовок повторяет' in issue for issue in result['warnings']))
            prompt=json.loads(model.call_args.args[0]['messages'][1]['content'])
            self.assertIn('莫斯科公寓',prompt['recentTitlesToAvoid'])
            with patch.object(content,'_model',return_value=response(second)):
                other=content.generate_preview(self.directory,{'fields':fields,'groupId':'other-flat'})
            self.assertEqual(other['status'],'review')
        memory=LearningMemory(self.directory)
        self.assertEqual(memory.status()['counts']['pending'],4)
        self.assertEqual(memory.status()['approvedExamples'],0)
        self.assertEqual(memory.retrieve('莫斯科公寓'),[])
        snapshot=memory.snapshot('pending-generated')
        self.assertEqual(snapshot['manifest']['trainExamples'],0)
        self.assertEqual(snapshot['manifest']['validExamples'],0)
    def test_current_title_is_avoided_without_existing_generated_history(self):
        baseline={'titles':['莫斯科40㎡'],'body':'月租80000卢布，配有沙发。','tags':'#莫斯科租房'}
        generated={'status':'review','model':'fixture','text':json.dumps({'title':'莫斯科｜公寓🌟','body':'40㎡，月租80000卢布，配有沙发。'},ensure_ascii=False)}
        with patch.object(language_service,'_profile',return_value={}),patch.object(language_service,'node_language',return_value=baseline),patch.object(content,'_model',return_value=generated) as model:
            result=content.generate_preview(self.directory,{'fields':{'price':'80000','area':'40'},'groupId':'one',
                'output':{'title':'莫斯科公寓','body':'其他说明'}})
        self.assertEqual(result['status'],'blocked')
        prompt=json.loads(model.call_args.args[0]['messages'][1]['content'])
        self.assertEqual(prompt['previousTitleToAvoid'],'莫斯科公寓')
    def test_unknown_claim_and_swapped_unit_block(self):
        baseline={'titles':['40㎡，8万卢布'],'body':'配有沙发'}
        self.assertTrue(content.validate_draft({'title':'40万卢布','body':'8㎡，配有冰箱'},baseline))
        self.assertTrue(content.validate_draft({'title':'莫斯科','body':'This is a desk and a chair'},baseline,{'price':'80000','area':'40'}))
        self.assertEqual(content.validate_draft({'title':'莫斯科40㎡','body':'莫斯科公寓，40㎡，月租80000卢布，配有沙发。'},
            {'titles':['莫斯科40㎡'],'body':'月租8万卢布，配有沙发。'}, {'price':'80000','area':'40'}),[])
        self.assertEqual(content.validate_draft({'title':'莫斯科40㎡｜8W/月','body':'莫斯科公寓，40㎡，月租80,000₽，配有沙发。'},
            {'titles':['莫斯科40㎡'],'body':'月租8万卢布，配有沙发。'}, {'price':'80000','area':'40'}),[])
    def test_bilingual_approved_memory_and_pending_is_never_used(self):
        memory=LearningMemory(self.directory)
        memory.record('translation',{'ru':'есть стол','zh':'有桌子'},status='approved')
        with patch.object(content,'_model') as model:
            self.assertEqual(content.translate_preview(self.directory,{'text':'有桌子','direction':'zh-ru'})['text'],'есть стол')
            model.assert_not_called()
        memory.record('translation',{'ru':'непроверено','zh':'未核实'})
        with patch.object(content,'_model',return_value={'status':'unavailable','message':'fixture'}):
            self.assertEqual(content.translate_preview(self.directory,{'text':'непроверено'})['status'],'unavailable')
    def test_active_custom_rules_are_exact_and_approved_memory_has_precedence(self):
        with patch.object(language_service,'_profile',return_value={'rules':{'рабочий стол':'办公桌'}}),patch.object(content,'_model') as model:
            result=content.translate_preview(self.directory,{'text':'рабочий стол','direction':'ru-zh'})
            self.assertEqual(result['text'],'办公桌');self.assertEqual(result['model'],'active-profile-rule');model.assert_not_called()
            self.assertFalse(result['knowledgeContext']['modelWeightsUpdated'])
            LearningMemory(self.directory).record('dictionary',{'ru':'рабочий стол','zh':'书桌'},status='approved')
            result=content.translate_preview(self.directory,{'text':'рабочий стол','direction':'ru-zh'})
            self.assertEqual(result['text'],'书桌');self.assertEqual(result['model'],'approved-memory')
    def test_partial_custom_rule_matches_reach_bounded_prompt_and_fallback(self):
        rules={f'предмет{index}':f'家具{index}' for index in range(12)}
        text=' '.join(rules)
        with patch.object(language_service,'_profile',return_value={'rules':rules}), \
                patch.object(content,'_model',return_value={'status':'review','text':'错误'}) as model, \
                patch.object(content,'_fallback_translate',return_value=None) as fallback:
            result=content.translate_preview(self.directory,{'text':text,'direction':'ru-zh'})
        context=json.loads(model.call_args.args[0]['messages'][1]['content'])['approvedGlossary']
        self.assertEqual(len(context),8);self.assertEqual(len(fallback.call_args.args[1]),8)
        self.assertEqual(result['knowledgeContext']['openLexiconCandidatesUsed'],0)
    def test_empty_wrong_language_or_changed_numbers_are_blocked(self):
        for output in ('','still Russian','租金90000卢布'):
            with self.subTest(output=output),patch.object(content,'_model',return_value={'status':'review','text':output}),patch.object(content,'_fallback_translate',return_value=None):
                self.assertEqual(content.translate_preview(self.directory,{'text':'Аренда 80000 рублей'})['status'],'blocked')
    def test_translation_routes_block_reversed_equipment_negation(self):
        with patch.object(content,'_model',return_value={'status':'review','text':'公寓配有冰箱。'}),patch.object(content,'_fallback_translate',return_value=None):
            result=content.translate_preview(self.directory,{'text':'В квартире нет холодильника.','direction':'ru-zh'})
        self.assertEqual(result['status'],'blocked')
        self.assertTrue(any('fridge' in issue for issue in result['warnings']))
    def test_failed_ru_zh_model_can_use_checked_existing_translation_route(self):
        with patch.object(content,'_model',return_value={'status':'review','model':'fixture-model','text':'月租90000卢布。'}), \
                patch.object(content,'_fallback_translate',return_value={'text':'月租80000卢布。'}) as fallback:
            result=content.translate_preview(self.directory,{'text':'Аренда80000 рублей в месяц.','direction':'ru-zh'})
        self.assertEqual(result['status'],'review');self.assertEqual(result['backend'],'nmt-pivot')
        self.assertEqual(result['fallbackFrom'],'fixture-model');self.assertFalse(result['autoApply'])
        self.assertLessEqual(fallback.call_args.args[-1],10)
    def test_fallback_must_pass_same_checks_and_does_not_claim_reverse_support(self):
        with patch.object(content,'_model',return_value={'status':'review','text':'错误'}), \
                patch.object(content,'_fallback_translate',return_value={'text':'月租90000卢布。'}):
            result=content.translate_preview(self.directory,{'text':'Аренда80000 рублей в месяц.','direction':'ru-zh'})
            self.assertEqual(result['status'],'blocked');self.assertIn('alternativeIssues',result['qualityChecks'])
        with patch.object(content,'_model',return_value={'status':'review','text':'错误'}), \
                patch.object(content,'_fallback_translate') as fallback:
            result=content.translate_preview(self.directory,{'text':'月租80000卢布。','direction':'zh-ru'})
            self.assertEqual(result['status'],'blocked');fallback.assert_not_called()
    def test_fallback_timeout_is_a_blocked_alternative_not_a_route_crash(self):
        import subprocess
        with patch.object(language_service,'machine_translate',side_effect=subprocess.TimeoutExpired('fixture',1)):
            self.assertIsNone(content._fallback_translate('Пример',[],self.directory,2))
    def test_generated_contact_identity_is_blocked_while_generic_message_invitation_is_allowed(self):
        baseline={'titles':['莫斯科公寓'],'body':'莫斯科公寓，40㎡，月租80000卢布。'}
        fields={'price':'80000','area':'40'}
        for suffix in ('微信号：fictional_id','Weixin fictional_id','VX fictional_id'):
            with self.subTest(contact=suffix):
                issues=content.validate_draft({'title':'莫斯科公寓','body':baseline['body']+suffix},baseline,fields)
                self.assertTrue(any('контакт' in issue for issue in issues))
        self.assertEqual(content.validate_draft({'title':'莫斯科公寓','body':baseline['body']+'欢迎私信了解。'},baseline,fields),[])

    def test_saved_card_stays_successful_when_learning_disk_fails(self):
        card={'fields':{},'titles':['标题'],'body':'原文','tags':'#莫斯科租房'}
        identifier=local_server.save_result(self.directory,card)
        with patch('learning_capture.capture_edit',side_effect=OSError('fixture unavailable')):
            result=language_service.edit_text(self.directory,identifier,{'part':'body','text':'新的正文'})
        self.assertEqual(result['body'],'新的正文')
        self.assertEqual(result['learningCapture']['status'],'unavailable')
        self.assertEqual(local_server.read_result(self.directory,identifier)['body'],'新的正文')
    def test_editor_cannot_enable_training_but_can_capture(self):
        self.assertFalse(access_roles.phone_route_allowed('editor','POST','learning',{'action':'scheduler-config','enabled':True}))
        self.assertTrue(access_roles.phone_route_allowed('editor','POST','learning',{'action':'record'}))
        self.assertFalse(access_roles.phone_route_allowed('viewer','GET','learning'))

if __name__=='__main__':unittest.main()
