import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from client_publication import sanitize_text,contains_commission,client_card,PublicationReviewRequired,PUBLICATION_POLICY_VERSION

class ClientPublicationPolicyTests(unittest.TestCase):
    def test_client_exports_cannot_relabel_explicit_daily_or_foreign_payment_units(self):
        for fields in ({'price':3500,'pricePeriod':'day'},{'price':80000,'priceCurrency':'unknown'},{'deposit':1000,'depositCurrency':'USD'}):
            with self.subTest(fields=fields),self.assertRaises(PublicationReviewRequired):client_card({'fields':fields,'body':'租金80,000₽'})
        self.assertEqual(client_card({'fields':{'price':80000,'deposit':0,'depositCurrency':'unknown'},'body':'租金80,000₽'})['body'],'租金80,000₽')

    def test_private_payment_calculation_and_raw_fees_are_never_client_fields(self):
        raw={'body':'租金80,000₽','fields':{'price':'80000','deposit':'40000','oneTimeFees':[{'label':'Комиссия','amount':'40000'}],'initialPayment':{'total':160000},'paymentCalculation':{'fee':40000}}}
        result=client_card(raw)
        self.assertEqual(result['fields'],{'price':'80000','deposit':'40000'})
        self.assertEqual(raw['fields']['initialPayment']['total'],160000)

    def test_shared_language_and_financial_cases(self):
        cases=json.loads((Path(__file__).parent/'fixtures/client-publication-policy.json').read_text())
        for row in cases:
            with self.subTest(row=row['input']):
                if row.get('review'):
                    with self.assertRaises(PublicationReviewRequired) as error:sanitize_text(row['input'])
                    self.assertIn(error.exception.fragment,row['input'])
                else:self.assertEqual(sanitize_text(row['input']),row['output']);self.assertFalse(contains_commission(row['output']))

    def test_derived_projection_preserves_private_inputs_and_is_idempotent(self):
        card={'body':'租金65000\n中介费50%\n押金35000','fields':{'price':'65000','deposit':'35000','commission':50,'clientCommissionPct':50,'clientFee':50,'source':'KEEP COMMISSION','sourceAudit':{'x':'KEEP'},'description':'押金35000\n佣金50%'}}
        before=json.dumps(card,sort_keys=True);result=client_card(card)
        self.assertEqual(json.dumps(card,sort_keys=True),before);self.assertEqual(result['body'],'租金65000\n押金35000')
        self.assertEqual(result['fields'],{'price':'65000','deposit':'35000','description':'押金35000'})
        self.assertEqual(result['publicationPolicyVersion'],PUBLICATION_POLICY_VERSION);self.assertEqual(client_card(result),result)

    def test_null_legacy_text_is_normalized_only_in_projection(self):
        raw={'body':None,'tags':None,'titles':None};result=client_card(raw)
        self.assertEqual(result,{'body':'','tags':'','titles':[],'publicationPolicyVersion':PUBLICATION_POLICY_VERSION})
        self.assertEqual(raw,{'body':None,'tags':None,'titles':None})

    def test_unresolved_claims_block_old_body_but_not_clean_text(self):
        fields={'deposit':'','buildingType':'','sourceAudit':{'blockedFields':['deposit','buildingType']}}
        for text in ['押金：35000₽','кирпичный дом','板式住宅']:
            with self.assertRaises(PublicationReviewRequired):client_card({'fields':fields,'body':text})
        self.assertEqual(client_card({'fields':fields,'body':'租金65000\n52㎡'})['body'],'租金65000\n52㎡')
        self.assertEqual(client_card({'fields':fields,'body':'押金待确认'})['body'],'押金待确认')

    def test_listing_envelope_does_not_inherit_raw_source(self):
        raw={'body':'租金65000','listing':{'url':'https://www.cian.ru/rent/flat/1/','images':[],'text':'Квартира52м² комиссия50%','fields':{'commission':50}}}
        safe=client_card(raw)
        self.assertEqual(safe['listing'],{'url':raw['listing']['url'],'images':[]});self.assertEqual(raw['listing']['text'],'Квартира52м² комиссия50%')

if __name__=='__main__':unittest.main()
