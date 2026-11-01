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
        self.assertEqual(LearningMemory(self.directory).status()['counts']['pending'],1)
        with patch.object(language_service,'_profile',return_value={}),patch.object(language_service,'node_language',return_value=baseline),patch.object(content,'_model',return_value=generated):
            repeated=content.generate_preview(self.directory,{'fields':fields})
        self.assertEqual(repeated['status'],'blocked');self.assertIn('повторяет',' '.join(repeated['warnings']))
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
    def test_empty_wrong_language_or_changed_numbers_are_blocked(self):
        for output in ('','still Russian','租金90000卢布'):
            with self.subTest(output=output),patch.object(content,'_model',return_value={'status':'review','text':output}):
                self.assertEqual(content.translate_preview(self.directory,{'text':'Аренда 80000 рублей'})['status'],'blocked')
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
