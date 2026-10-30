"""Language operations preserve unrelated edits and use existing auth boundaries."""
import copy
import gzip
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import language_service as language
import local_server
import phone_relay
import test_card_api as route_fixture


class LanguageUpdates(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.original={'fields':{'price':'80000','rooms':'2','photos':[],
            'description':'Есть шкаф и рабочий стол','titleVariant':7,'customFact':'preserve'},
            'titles':['手工标题','备选标题'],'body':'手工正文','tags':'#手工标签',
            'photoAnalysis':{'original':'kept'},'templateInfo':{'titleId':'old-title',
            'descriptionId':'old-body','tagId':'old-tags','manualParts':['body','tags']}}
        self.card=local_server.save_result(self.directory,self.original)
        phone_relay.save_profile(self.directory,{'rules':{'кодовое слово':'自定义'},'prefs':{'contact':'联系我'}})
        language._capabilities.clear()

    def tearDown(self):self.temp.cleanup()

    @staticmethod
    def render(payload):
        fields=payload['fields'];indices={'title':fields['titleVariant'],
            'description':fields['copyVariant'],'tags':fields['tagVariant']}
        return {'titles':['标题'+str(indices['title'])],'body':'正文'+str(indices['description']),
            'tags':'#标签'+str(indices['tags']),'templateInfo':{'version':1,'mode':'catalog',
            'indices':indices,'titleId':'title-'+str(indices['title']),
            'titleIds':['title-'+str(indices['title'])],'descriptionId':'body-'+str(indices['description']),
            'tagId':'tags-'+str(indices['tags']),'counts':{'titles':262144}}}

    def test_independent_rewrites_preserve_manual_text_facts_and_other_template_ids(self):
        with patch.object(language,'node_language',side_effect=self.render) as render:
            result=language.rewrite(self.directory,self.card,{'part':'title'})
            self.assertEqual(result['id'],self.card);self.assertEqual(result['titles'],['标题8'])
            self.assertEqual(result['body'],self.original['body']);self.assertEqual(result['tags'],self.original['tags'])
            self.assertEqual(result['templateInfo']['descriptionId'],'old-body')
            self.assertEqual(result['templateInfo']['tagId'],'old-tags')
            self.assertEqual(result['templateInfo']['indices'],{'title':8,'description':7,'tags':7})
            self.assertNotIn('copyVariant',result['fields']);self.assertNotIn('tagVariant',result['fields'])
            self.assertEqual(render.call_args.args[0]['rules'],{'кодовое слово':'自定义'})
            result=language.rewrite(self.directory,self.card,{'part':'tags'})
            self.assertEqual(result['titles'],['标题8']);self.assertEqual(result['body'],'手工正文')
            self.assertEqual(result['tags'],'#标签8');self.assertEqual(result['templateInfo']['indices']['description'],7)
            result=language.rewrite(self.directory,self.card,{'part':'body'})
            self.assertEqual(result['body'],'正文8');self.assertEqual(result['titles'],['标题8'])
            self.assertEqual(result['photoAnalysis'],self.original['photoAnalysis'])
            self.assertEqual(result['fields']['customFact'],'preserve')
            self.assertEqual(result['fields']['description'],self.original['fields']['description'])

    def test_manual_preview_does_not_save_then_apply_keeps_other_parts_and_russian_source(self):
        translated={'text':'有衣柜和书桌','unknown':[],'warnings':[]}
        with patch.object(language,'node_language',return_value=translated) as render:
            self.assertEqual(language.translate(self.directory,{'text':'Есть шкаф и стол','rules':{'кодовое слово':'evil'}}),translated)
            self.assertEqual(render.call_args.args[0]['rules'],{'кодовое слово':'自定义'})
            self.assertEqual(local_server.read_result(self.directory,self.card),self.original)
        result=language.edit_text(self.directory,self.card,{'part':'body','text':'我的完整正文','sourceRu':'Есть шкаф и стол'})
        self.assertEqual(result['body'],'我的完整正文');self.assertEqual(result['fields']['description'],'Есть шкаф и стол')
        self.assertEqual(result['titles'],self.original['titles']);self.assertEqual(result['tags'],self.original['tags'])
        self.assertNotIn('descriptionId',result['templateInfo']);self.assertEqual(result['templateInfo']['titleId'],'old-title')
        with patch.object(language,'node_language',side_effect=self.render):
            result=language.rewrite(self.directory,self.card,{'part':'title'})
        self.assertEqual(result['body'],'我的完整正文');self.assertIn('body',result['templateInfo']['manualParts'])

    def test_concurrent_edit_returns_conflict_instead_of_overwriting(self):
        newer=copy.deepcopy(self.original);newer['body']='更新的手工正文'
        def render(payload):
            local_server.save_result(self.directory,newer,self.card)
            return self.render(payload)
        with patch.object(language,'node_language',side_effect=render):
            status,result,_,_=language.dispatch(self.directory,'cards/'+self.card+'/rewrite',{'part':'title'})
        self.assertEqual(status,409);self.assertIn('изменилась',result['error'])
        self.assertEqual(local_server.read_result(self.directory,self.card),newer)

    def test_limits_missing_cards_and_cached_capabilities(self):
        with patch.object(language,'node_language',return_value={'templates':{'titles':262144}}) as render:
            for data in ({'part':'title','text':'x'*241},{'part':'body','text':'x'*20001},
                         {'part':'tags','text':'x'*3001},{'part':'body','text':'a','sourceRu':'x'*20001}):
                with self.assertRaises(ValueError):language.edit_text(self.directory,self.card,data)
            with self.assertRaises(ValueError):language.translate(self.directory,{'text':'x'*20001})
            with self.assertRaises(ValueError):language.search(self.directory,{'query':'x','offset':-1})
            render.assert_not_called()
            first=language.capabilities(self.directory);first['templates']['titles']=0
            self.assertEqual(language.capabilities(self.directory)['templates']['titles'],262144)
            self.assertEqual(render.call_count,1)
        self.assertEqual(language.dispatch(self.directory,'cards/'+'f'*32+'/rewrite',{'part':'all'})[0],404)
        self.assertEqual(local_server.read_result(self.directory,self.card),self.original)

    def test_optional_machine_preview_is_reviewable_bounded_and_never_persisted(self):
        preview={'text':'不熟悉的 оборот','unknown':['оборот'],'warnings':[],
            'coverage':{'ratio':.5},'matches':[],'backend':'rental-phrases'}
        with patch.object(language,'node_language',return_value=preview), \
             patch.object(language,'machine_translate',return_value={'text':'需要核实的表达',
                'warnings':['Проверьте сумму'],'backend':'nmt-pivot'}) as machine:
            result=language.translate(self.directory,{'text':'Незнакомый оборот'})
            self.assertEqual(result['text'],'需要核实的表达');self.assertTrue(result['needsReview'])
            self.assertEqual(result['unknown'],[]);self.assertEqual(result['backend'],'nmt-pivot')
            self.assertEqual(machine.call_args.args[1],{'кодовое слово':'自定义'})
            self.assertLessEqual(machine.call_args.args[2],52)
            self.assertEqual(local_server.read_result(self.directory,self.card),self.original)
        with patch.object(language,'node_language',return_value=preview), \
             patch.object(language,'machine_translate',side_effect=language.subprocess.TimeoutExpired('worker',52)):
            result=language.translate(self.directory,{'text':'Незнакомый оборот'})
            self.assertEqual(result['text'],preview['text']);self.assertEqual(result['unknown'],preview['unknown'])
            self.assertTrue(any('не завершился' in item for item in result['warnings']))
        with patch.object(language,'node_language',return_value=preview), \
             patch.object(language,'machine_translate') as machine:
            result=language.translate(self.directory,{'text':'x'*5001})
            machine.assert_not_called();self.assertTrue(any('5000' in item for item in result['warnings']))

    def test_optional_mac_runtime_uses_native_arm_even_with_translated_server(self):
        responses=[SimpleNamespace(returncode=0,stdout='{"available":true}'),
            SimpleNamespace(returncode=0,stdout='{"text":"中文","warnings":[]}')]
        with patch.object(language.platform,'system',return_value='Darwin'), \
             patch.object(Path,'is_file',return_value=True), \
             patch.object(language.subprocess,'run',side_effect=responses) as run:
            result=language.machine_translate('Текст',{'ключ':'翻译'},52)
        self.assertEqual(result['text'],'中文')
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:2],['/usr/bin/arch','-arm64'])
        self.assertEqual(run.call_args_list[0].args[0][-1],'--status')
        self.assertEqual(json.loads(run.call_args_list[1].kwargs['input'])['rules'],{'ключ':'翻译'})

    def test_machine_glossary_preserves_reviewed_phrases_with_personal_rules_first(self):
        phone_relay.save_profile(self.directory,{'rules':{'залог':'自定义押金'},'prefs':{}})
        preview={'text':'押金 и 准确','unknown':['оборот'],'warnings':[],
            'matches':[{'ru':'залог','zh':'押金','reviewed':True},
                {'ru':'есть рабочий стол','zh':'配有书桌','reviewed':True},
                {'ru':'оборот','zh':'不确定','reviewed':False}]}
        with patch.object(language,'node_language',return_value=preview), \
             patch.object(language,'machine_translate',return_value={'text':'自定义押金，配有书桌','warnings':[]}) as machine:
            language.translate(self.directory,{'text':'Залог; есть рабочий стол. Незнакомый оборот'})
        self.assertEqual(machine.call_args.args[1],{'залог':'自定义押金','есть рабочий стол':'配有书桌'})


class AuthenticatedLanguageRoutes(unittest.TestCase):
    setUp=route_fixture.CardDocumentRoutes.setUp
    tearDown=route_fixture.CardDocumentRoutes.tearDown
    request=route_fixture.CardDocumentRoutes.request

    def test_phone_auth_and_csrf_are_checked_before_dictionary_or_card_operations(self):
        endpoints=[('language',None),('language/search',{'query':'шкаф'}),('translate',{'text':'шкаф'}),
            ('cards/'+self.card+'/rewrite',{'part':'title'}),('cards/'+self.card+'/edit-text',{'part':'tags','text':'#标签'})]
        with patch.object(language,'node_language',return_value={'text':'衣柜'}) as render:
            for path,data in endpoints:
                self.assertEqual(self.request(path,data,auth=False)[0],401,path)
                if data is not None:self.assertEqual(self.request(path,data,csrf=False)[0],403,path)
            render.assert_not_called()
            status,body,_=self.request('translate',{'text':'шкаф'})
            self.assertEqual(status,200,body);self.assertEqual(json.loads(body)['text'],'衣柜')
            status,body,_=self.request('cards/'+self.card+'/edit-text',{'part':'tags','text':'#自己的标签'})
            self.assertEqual(status,200,body);self.assertEqual(json.loads(body)['tags'],'#自己的标签')

    def test_desktop_token_protects_search_and_catalog_and_phone_exports_binary(self):
        base='http://127.0.0.1:'+str(self.desktop.server_port)
        page=urllib.request.urlopen(base+'/').read().decode()
        token=re.search(r'name="redcn-token" content="([^"]+)"',page)[1]
        for path in ('language','language/catalog/title'):
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+'/api/local/'+path)
            self.assertEqual(caught.exception.code,403)
        with patch.object(language,'node_language',return_value={'entries':[],'total':0,'offset':0}) as render:
            request=urllib.request.Request(base+'/api/local/language/search',data=json.dumps({'query':'шкаф'}).encode(),
                headers={'Content-Type':'application/json','X-Redcn-Token':token})
            with urllib.request.urlopen(request) as response:self.assertEqual(json.loads(response.read())['total'],0)
            self.assertEqual(render.call_args.args[0]['action'],'search')
        self.assertEqual(self.request('language/catalog/title',auth=False)[0],401)
        status,body,headers=self.request('language/catalog/title')
        self.assertEqual(status,200);self.assertIn('attachment;',headers['Content-Disposition'])
        self.assertEqual(body[:2],b'\x1f\x8b');self.assertIn(b'"',gzip.decompress(body)[:100])


if __name__=='__main__':unittest.main()
