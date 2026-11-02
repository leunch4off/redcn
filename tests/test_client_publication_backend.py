"""Final client boundaries sanitize old drafts and gate unverifiable old assets."""
import base64
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_api
import card_assets
import local_server
import phone_relay
import photo_store
from client_publication import PUBLICATION_POLICY_VERSION,PublicationReviewRequired

SAFE_HTML='<!doctype html><html><head><meta charset="utf-8"></head><body><main><p>租金：80000 ₽；押金：40000 ₽；水电另付。</p></main></body></html>'
PHOTO='https://images.cdn-cian.ru/images/publication-boundary.jpg'

class ClientPublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.raw={'fields':{'price':'80000','deposit':'40000','commission':'50','source':'Без комиссии. Аренда 80000 ₽','sourceAudit':{'commission':50},'photos':[PHOTO]},'titles':['Москва · без комиссии'],'body':'租金：80000 ₽\n🤝 中介费：无需支付中介费\n押金：40000 ₽\n水电另付','tags':'#莫斯科 #免中介费'}
        self.card_id=local_server.save_result(self.directory,self.raw)

    def tearDown(self):self.temp.cleanup()

    def image(self,metadata=False):
        from PIL import Image,PngImagePlugin
        stream=io.BytesIO();info=PngImagePlugin.PngInfo();info.add_text('Internal','佣金50%')
        Image.new('RGB',(80,80),'#6a826e').save(stream,'PNG',**({'pnginfo':info} if metadata else {}));return stream.getvalue()

    def test_phone_public_card_and_zip_omit_old_commission_but_leave_saved_draft_unchanged(self):
        png=self.image();photo_store.attach_photo(self.directory,PHOTO,'data:image/png;base64,'+base64.b64encode(png).decode())
        before=local_server.read_result(self.directory,self.card_id)
        public=phone_relay.public_card(self.directory,self.card_id)
        self.assertNotIn('commission',public['fields']);self.assertNotIn('source',public['fields']);self.assertNotIn('中介费',public['body']);self.assertNotIn('免中介费',public['tags'])
        self.assertIn('80000',public['body']);self.assertIn('40000',public['body']);self.assertIn('水电另付',public['body'])
        bundle=zipfile.ZipFile(io.BytesIO(phone_relay.card_zip(self.directory,self.card_id)))
        self.assertNotIn('中介费',bundle.read('post.txt').decode());self.assertEqual(bundle.read('01.png'),png)
        self.assertEqual(local_server.read_result(self.directory,self.card_id),before)
        feed=phone_relay.list_cards(self.directory)['cards'];self.assertEqual(len(feed),1);self.assertNotIn('комисси',feed[0]['title'])

    def test_html_pdf_reject_hidden_metadata_and_commission_split_across_tags_before_render(self):
        candidates=[SAFE_HTML.replace('</main>','<p>中介费：0</p></main>'),SAFE_HTML.replace('</main>','<p><b>中介</b><span>费：50%</span></p></main>'),SAFE_HTML.replace('<meta charset="utf-8">','<meta charset="utf-8"><meta name="description" content="Без комиссии">'),SAFE_HTML.replace('</main>','<img alt="佣金50%" src="data:image/png;base64,'+base64.b64encode(self.image()).decode()+'"></main>'),SAFE_HTML.replace('</head>','<style>p::after{content:"中介费"}</style></head>')]
        for document in candidates:
            with self.subTest(document=document[:100]),patch.object(card_assets,'_render_pdf') as render,self.assertRaises(PublicationReviewRequired):
                card_assets.create_export(self.directory,self.card_id,document,'pdf')
            render.assert_not_called()
        self.assertEqual(card_assets.list_exports(self.directory,self.card_id),[])

    def test_old_materials_are_retained_but_require_regeneration_and_new_html_is_certified(self):
        legacy=card_assets._persist_export(self.directory,self.card_id,b'%PDF-old-private','pdf','application/pdf')
        with self.assertRaisesRegex(ValueError,'Создайте его заново'):card_assets.export_asset(self.directory,legacy['id'])
        self.assertEqual(card_assets._read(self.directory,card_assets._write(self.directory,b'%PDF-old-private')),b'%PDF-old-private')
        safe=card_assets.create_export(self.directory,self.card_id,SAFE_HTML,'html')
        self.assertEqual(card_assets.export_asset(self.directory,safe['id'])[0],SAFE_HTML.encode())
        rows={r['id']:r for r in card_assets.list_exports(self.directory,self.card_id)}
        self.assertTrue(rows[legacy['id']]['requiresRegeneration']);self.assertFalse(rows[safe['id']]['requiresRegeneration'])
        self.assertEqual(rows[safe['id']]['publicationPolicyVersion'],PUBLICATION_POLICY_VERSION)
        with card_assets._db(self.directory) as db:db.execute('UPDATE export_publication_policy SET digest=? WHERE export_id=?',('f'*64,safe['id']))
        with self.assertRaisesRegex(ValueError,'Создайте его заново'):card_assets.export_asset(self.directory,safe['id'])

    def test_raster_requires_updated_renderer_contract_and_discards_png_text_metadata(self):
        data=base64.b64encode(self.image(metadata=True)).decode()
        with self.assertRaisesRegex(ValueError,'Обновите страницу'):card_assets.create_image_export(self.directory,self.card_id,data,'intro')
        receipt=phone_relay.public_card(self.directory,self.card_id)['presentationReceipt'];revision=receipt['revisionSha256']
        response=card_api.dispatch(self.directory,'card-export',{'result_id':self.card_id,'data':data,'kind':'intro','publicationPolicyVersion':PUBLICATION_POLICY_VERSION,'expectedRevision':revision,'sourceReceipt':receipt})
        self.assertEqual(response[0],200,response[1])
        asset=response[1];content=card_assets.export_asset(self.directory,asset['id'])[0]
        from PIL import Image
        with Image.open(io.BytesIO(content)) as image:self.assertNotIn('Internal',image.info);self.assertEqual(image.size,(80,80))

    def test_mixed_financial_draft_requires_operator_review_without_blocking_feed_or_mutating_source(self):
        self.raw['body']='Аренда 80000 ₽ и комиссия 50% включены в первый платёж'
        local_server.save_result(self.directory,self.raw,self.card_id)
        before=local_server.read_result(self.directory,self.card_id)
        self.assertEqual(len(phone_relay.list_cards(self.directory)['cards']),1)
        with self.assertRaises(PublicationReviewRequired) as caught:phone_relay.public_card(self.directory,self.card_id)
        self.assertIn('80000',caught.exception.fragment)
        with patch.object(card_assets,'_render_pdf') as render,self.assertRaises(PublicationReviewRequired):card_assets.create_export(self.directory,self.card_id,SAFE_HTML,'pdf')
        render.assert_not_called();self.assertEqual(local_server.read_result(self.directory,self.card_id),before)

    def test_feed_manual_summary_and_null_or_string_titles_are_safe_without_altering_drafts(self):
        self.raw['titles']=None
        self.raw['fields'].update({'metro':'Метро Саларьево и комиссия 50% включены в платёж','price':'Аренда 80000 ₽ и комиссия 50%','area':'52 м²; комиссия 50%'})
        local_server.save_result(self.directory,self.raw,self.card_id)
        second=local_server.save_result(self.directory,{'titles':'Новая квартира','fields':{'price':90000,'area':54,'metro':'Прокшино'},'body':'正文','tags':''})
        before=local_server.read_result(self.directory,self.card_id)
        rows={card['id']:card for card in phone_relay.list_cards(self.directory)['cards']}
        unsafe=rows[self.card_id];self.assertEqual(unsafe['title'],'');self.assertEqual(unsafe['metro'],'');self.assertEqual(unsafe['price'],'');self.assertEqual(unsafe['area'],'52 м²');self.assertTrue(unsafe['publicationNeedsReview'])
        self.assertEqual(rows[second]['title'],'Новая квартира');self.assertEqual(rows[second]['price'],90000)
        self.assertNotIn('комисси',json.dumps(rows,ensure_ascii=False))
        self.assertEqual(local_server.read_result(self.directory,self.card_id),before)

class PhotoPublicationProvenanceTests(unittest.TestCase):
    def test_layout_is_not_a_room_photo_and_known_source_metadata_reaches_loader(self):
        layout='https://images.cdn-cian.ru/images/layout.jpg'
        preview='https://images.cdn-cian.ru/images/layout-small.jpg'
        source={'url':PHOTO,'photoId':'1080967826','order':0,'isLayout':False,'variants':{'fullUrl':PHOTO}}
        card={'fields':{'photos':[PHOTO,layout,preview],'photoItems':[source,{'url':layout,'isLayout':True,'isCianLayout':True,'variants':{'thumbnailUrl':preview}}]}}
        self.assertEqual(phone_relay.source_images(card),[PHOTO])
        with tempfile.TemporaryDirectory() as folder,patch.object(photo_store,'load_photo',return_value={'id':'a'*64,'sourceUrl':PHOTO}) as load,patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            phone_relay.cache_photos(Path(folder),card)
        self.assertEqual(load.call_args.args,(Path(folder),PHOTO));self.assertEqual(load.call_args.kwargs,{'source':source})

    def test_old_card_without_metadata_keeps_existing_loader_signature(self):
        card={'fields':{'photos':[PHOTO]}}
        with tempfile.TemporaryDirectory() as folder,patch.object(photo_store,'load_photo',return_value={'id':'a'*64,'sourceUrl':PHOTO}) as load,patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            phone_relay.cache_photos(Path(folder),card)
        self.assertEqual(load.call_args.kwargs,{})


if __name__=='__main__':unittest.main()
