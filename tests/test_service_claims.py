"""Current card/service scopes gate client text and supplied HTML/PDF exports."""
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_assets
import client_publication
import local_server
import test_phone_relay as phone_fixture
from service_claims import ServiceClaimReview,assert_service_claims,service_card_key,service_claim_state,service_authorization


def iso(stamp):return datetime.fromtimestamp(stamp,timezone.utc).isoformat().replace('+00:00','Z')


def fields():
    return {'editorIdentity':'owned-service-fixture','price':'80000','registrationMonths':'6','registrationPeople':'3','pickup':True,'luggage':True,'arrival':True,'support':True,'serviceBenefits':'✅ 免费接送到家\n✅ 中文租赁文件翻译'}


def receipt_fields(stamp=None):
    value=fields();at=iso(time.time() if stamp is None else stamp);key=service_card_key(value)
    values={'registration':{'service':'arrival-registration-assistance','months':'6','people':'3'},'pickup':{'included':True,'luggage':True},'arrival':{'included':True},'support':{'included':True,'text':value['serviceBenefits']}}
    value['serviceConfirmations']={name:{'actor':'PRIVATE_OPERATOR','at':at,'cardKey':key,'value':scope} for name,scope in values.items()}
    return value


def card(value=None,body='抵达登记（临时居住登记）：提供办理协助。'):
    return {'fields':fields() if value is None else value,'titles':['测试房源'],'body':body,'tags':'#莫斯科租房'}


class ServiceClaimTests(unittest.TestCase):
    def test_shared_seventeen_js_python_service_claim_cases(self):
        cases=json.loads((Path(__file__).parent/'fixtures/service-claim-cases.json').read_text())
        self.assertEqual(len(cases),17)
        for row in cases:
            value=receipt_fields() if row['confirmed'] else fields()
            value['serviceBenefits']='✅ 免费接送到家\n✅ 中文沟通'
            if row['confirmed']:value['serviceConfirmations']['support']['value']['text']=value['serviceBenefits']
            with self.subTest(name=row['name']):
                if row['allowed']:assert_service_claims(card(value,row['body']))
                else:
                    with self.assertRaises(ServiceClaimReview):assert_service_claims(card(value,row['body']))

    def test_neutral_assistance_remains_allowed_without_receipts_but_free_and_scoped_claims_are_gated(self):
        before=card();self.assertEqual(client_publication.client_card(before)['body'],before['body'])
        for text in ['免费接送到入住地点。','📋 免费协助了解抵达后的相关手续。','Free airport pickup.','Бесплатный трансфер до квартиры.','免费住宿登记。','抵达登记（临时居住登记）：提供办理协助（6个月），最多3人。']:
            value=card(body=text);original=copy.deepcopy(value)
            with self.subTest(text=text),self.assertRaises(client_publication.PublicationReviewRequired):client_publication.client_card(value)
            self.assertEqual(value,original)

    def test_current_card_exact_scopes_allow_promises_and_receipts_stay_private(self):
        value=receipt_fields();body='抵达登记（临时居住登记）：提供办理协助（6个月），最多3人。\n🚕 免费接送到入住地点，协助搬运行李。\n📋 免费协助了解抵达后的相关手续。\n🇨🇳 我们的优势：\n'+value['serviceBenefits']
        original=card(value,body);before=copy.deepcopy(original)
        self.assertTrue(all(service_claim_state(value).values()))
        projected=client_publication.client_card(original)
        self.assertEqual(projected['body'],body)
        self.assertNotIn('serviceConfirmations',projected['fields']);self.assertNotIn('serviceBenefits',projected['fields'])
        self.assertNotIn('PRIVATE_OPERATOR',json.dumps(projected));self.assertEqual(original,before)

    def test_scope_change_other_card_undated_future_expired_or_boolean_substitution_cannot_reuse_receipts(self):
        now=time.time();base=receipt_fields(now-10)
        cases=[]
        for key,new in [('registrationPeople','4'),('registrationMonths','12'),('editorIdentity','different-card'),('luggage',False),('pickup',False),('arrival',False),('serviceBenefits','Новый текст поддержки')]:
            changed=copy.deepcopy(base);changed[key]=new;cases.append(changed)
        for stamp in (None,iso(now+10),iso(now-30*86400-1)):
            changed=copy.deepcopy(base)
            for row in changed['serviceConfirmations'].values():row['at']=stamp
            cases.append(changed)
        changed=copy.deepcopy(base);changed['serviceConfirmations']['pickup']['value']['included']=1;cases.append(changed)
        for changed in cases:
            with self.subTest(changed=changed),self.assertRaises(ServiceClaimReview):
                assert_service_claims(card(changed,'抵达登记：6个月，最多3人。\n免费接送到家。\n免费协助抵达后的手续。\n我们的优势：'+base['serviceBenefits']),now=now)
        for name in ('registration','pickup','arrival','support'):
            changed=copy.deepcopy(base);changed['serviceConfirmations'][name].pop('cardKey')
            self.assertFalse(service_claim_state(changed,now=now)[name])

    def test_registration_scope_text_must_match_receipt_and_free_registration_is_never_inferred(self):
        confirmed=receipt_fields()
        for text in ['抵达登记：12个月，最多3人。','抵达登记：6个月，最多4人。','免费办理临时居住登记。','Free registration for 6 months.']:
            with self.subTest(text=text),self.assertRaises(ServiceClaimReview):assert_service_claims(card(confirmed,text))
        confirmed['url']='https://www.cian.ru/rent/flat/1/?tracking=ignored'
        self.assertEqual(service_card_key(confirmed),'url:https://www.cian.ru/rent/flat/1')
        self.assertFalse(service_claim_state(confirmed)['registration'])
        stable=receipt_fields();stable['resultId']='a'*32
        self.assertTrue(service_claim_state(stable)['registration'])
        self.assertEqual(service_card_key(stable),'editorIdentity:owned-service-fixture')

    def test_benefits_require_exact_current_text_and_do_not_authorize_extra_free_claims(self):
        confirmed=receipt_fields();confirmed['pickup']=False
        assert_service_claims(card(confirmed,'🇨🇳 我们的优势：\n'+confirmed['serviceBenefits']))
        for text in ['我们的优势：其他中文服务','我们的优势：'+confirmed['serviceBenefits']+'\n免费接送到另一城市。']:
            with self.subTest(text=text),self.assertRaises(ServiceClaimReview):assert_service_claims(card(confirmed,text))

    def test_html_pdf_composed_and_metadata_promises_are_checked_before_render_or_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);card_id=local_server.save_result(directory,card())
            for fragment in ['<p><span>免</span><b>费接送到家</b></p>','<img alt="免费接送到家" src="data:image/png;base64,PLACEHOLDER">','<p>抵达登记：6个月，最多3人。</p>']:
                # Text in actual static attrs is inspected too; no image decode
                # is needed to prove the composed-text service boundary.
                html='<!doctype html><html><body>'+fragment+'</body></html>'
                if 'PLACEHOLDER' in fragment:
                    with self.assertRaises(client_publication.PublicationReviewRequired):card_assets._assert_public_html(html,card())
                    continue
                with patch.object(card_assets,'_render_pdf') as render,self.assertRaises(client_publication.PublicationReviewRequired):card_assets.create_export(directory,card_id,html,'pdf')
                render.assert_not_called()
            self.assertEqual(card_assets.list_exports(directory,card_id),[])
            confirmed=receipt_fields();confirmed['pickup']=False
            confirmed_id=local_server.save_result(directory,card(confirmed))
            html='<!doctype html><html><body><h2>我们的优势：</h2><p>✅ 免费接送到家</p><p>✅ 中文租赁文件翻译</p></body></html>'
            saved=card_assets.create_export(directory,confirmed_id,html,'html')
            self.assertEqual(card_assets.export_asset(directory,saved['id'])[0],html.encode())


class ServiceClaimPhoneHTTP(unittest.TestCase):
    setUp=phone_fixture.PhoneRelayTests.setUp
    tearDown=phone_fixture.PhoneRelayTests.tearDown
    request=phone_fixture.PhoneRelayTests.request
    pair=phone_fixture.PhoneRelayTests.pair

    def test_legacy_feed_and_detail_remain_readable_with_safe_omission_but_zip_stays_blocked(self):
        value=card(body='租金：80000 ₽。\n免费接送到家。\n抵达登记：6个月，最多3人。\n\n我们的优势：\n✅ 免费接送到家\n✅ 中文沟通')
        value['serviceAuthorization']={'version':1,'expiresAt':iso(time.time()+30*86400)}
        identifier=local_server.save_result(self.directory,value);before=local_server.read_result(self.directory,identifier)
        self.pair()
        feed=self.request('/api/phone/cards')['cards'];self.assertEqual(len(feed),1);self.assertTrue(feed[0]['publicationNeedsReview'])
        detail=self.request('/api/phone/cards/'+identifier)
        self.assertTrue(detail['publicationNeedsReview']);self.assertIn('подтвердите',detail['publicationReviewReason'])
        self.assertIn('80000',detail['body']);self.assertIn('提供办理协助',detail['body']);self.assertNotIn('免费',detail['body'])
        self.assertNotIn('serviceAuthorization',detail)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/cards/'+identifier+'.zip')
        self.assertEqual(caught.exception.code,400)
        self.assertEqual(local_server.read_result(self.directory,identifier),before)

    def test_verified_detail_emits_public_expiry_without_receipt_data_or_trusting_incoming_marker(self):
        now=time.time();value=receipt_fields(now-10)
        value['serviceConfirmations']['registration']['at']=iso(now-100)
        original=card(value,'抵达登记：6个月，最多3人。\n免费接送到家。')
        original['serviceAuthorization']={'version':1,'expiresAt':'2099-01-01T00:00:00Z'}
        identifier=local_server.save_result(self.directory,original);self.pair()
        detail=self.request('/api/phone/cards/'+identifier)
        expected=service_authorization(value)
        self.assertEqual(detail['serviceAuthorization'],expected)
        self.assertEqual(set(detail['serviceAuthorization']),{'version','expiresAt'})
        self.assertNotIn('PRIVATE_OPERATOR',json.dumps(detail));self.assertNotIn('cardKey',json.dumps(detail))
        self.assertNotIn('serviceConfirmations',detail['fields']);self.assertNotIn('serviceBenefits',detail['fields'])
        with self.assertRaises(client_publication.PublicationReviewRequired):client_publication.client_card(detail)
        self.assertEqual(local_server.read_result(self.directory,identifier),original)


if __name__=='__main__':unittest.main()
