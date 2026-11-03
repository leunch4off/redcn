import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from source_schema_health import attach,evaluate,public_summary


class SourceSchemaHealthTests(unittest.TestCase):
    def test_missing_values_remain_unknown_and_original_fields_unchanged(self):
        original={'text':'Квартира с неизвестной ценой и площадью.','fields':{'price':'','area':None,'pets':''},'images':[]}
        before=copy.deepcopy(original)
        result=attach('<h1 data-name="NewUnsupportedOffer">Квартира без опубликованной цены</h1>',original,'https://www.cian.ru/')
        self.assertEqual(result['fields'],before['fields']);self.assertEqual(original,before)
        self.assertEqual(result['sourceSchemaHealth']['state'],'unsupported')
        self.assertIn('price',result['sourceSchemaHealth']['missingCritical'])
        self.assertEqual(result['fields']['pets'],'')

    def test_complete_known_contract_and_studio_zero_rooms(self):
        listing={'text':'Отдельная студия с мебелью и техникой.','fields':{'price':'85000','area':'32.5','rooms':'0','layout':'studio'},'images':['fixture']}
        raw='<h1 data-name="OfferTitleNew">Студия</h1><p data-name="Description">Квартира</p>'
        self.assertEqual(evaluate(raw,listing,'https://cian.ru/')['state'],'recognized')
        listing['fields']['sourceAudit']={'blockedFields':['price']}
        self.assertEqual(evaluate(raw,listing,'https://cian.ru/')['state'],'partial')

    def test_fragment_drops_scripts_contacts_secrets_and_export_summary_is_allowlist(self):
        raw='<script>PRIVATE_SCRIPT</script><div data-name="Description">Позвонить +7 (999) 123-45-67 x@example.com @contact token=SECRET website https://example.com/?token=OTHER</div>'
        value=evaluate(raw,{'text':'Описание квартиры для частной проверки.','fields':{},'images':[]},'https://cian.ru/')
        fragment=value['privateReviewFragment']
        for secret in ('PRIVATE_SCRIPT','999','x@example','@contact','SECRET','OTHER','https://'):self.assertNotIn(secret,fragment)
        safe=public_summary({**value,'evil':'PRIVATE','reasons':value['reasons']+['PRIVATE']})
        self.assertNotIn('privateReviewFragment',safe);self.assertNotIn('PRIVATE',str(safe));self.assertNotIn('sourceSha256',safe)

    def test_drift_contracts_on_both_observed_source_fixtures(self):
        root=Path(__file__).resolve().parent/'fixtures'
        listing={'text':'Достаточное описание вымышленной квартиры.','fields':{'price':'80000','area':'54','rooms':'2'},'images':['fixture']}
        for platform in ('cian','avito'):
            raw=(root/(platform+'-user-observed-v26.html')).read_text()
            observed=evaluate(raw,listing,'https://'+platform+'.ru/')
            self.assertEqual(observed['state'],'recognized')
            changed=raw.replace('data-name=','data-new-name=').replace('data-marker=','data-new-marker=')
            self.assertEqual(evaluate(changed,listing,'https://'+platform+'.ru/')['state'],'unsupported')

    def test_source_status_and_client_projection_do_not_publish_private_excerpt(self):
        from source_readiness import source_readiness
        from client_publication import client_card
        schema=evaluate('<p>PRIVATE_REVIEW_TEXT, квартира без площади.</p>',{'text':'Достаточное описание квартиры без цифр.','fields':{}},'https://cian.ru/')
        listing={'sourceSchemaHealth':schema}
        status=source_readiness(listing)
        self.assertTrue(status['partial']);self.assertFalse(status['completed'])
        self.assertNotIn('PRIVATE_REVIEW_TEXT',str(status))
        self.assertEqual(source_readiness({'sourceReadiness':status})['schema'],status['schema'])
        card=client_card({'fields':{'sourceSchemaHealth':schema},'sourceSchemaHealth':schema,'body':'房屋信息待确认。','titles':['房屋'],'tags':''})
        self.assertNotIn('PRIVATE_REVIEW_TEXT',str(card));self.assertNotIn('sourceSchemaHealth',str(card))


if __name__=='__main__':unittest.main()
