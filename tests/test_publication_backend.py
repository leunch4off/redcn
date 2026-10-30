"""Private reusable layout/brand assets and static print contract regressions."""
import base64
import copy
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import card_assets
import card_api
import local_server
import publication_schema
from node_runtime import resolve_node
import test_card_api as route_fixture

ROOT = Path(__file__).resolve().parents[1]


def design_fixture():
    value = subprocess.run([resolve_node(), '--input-type=module', '-e',
        "import {normalizePublicationDesign} from './web/publication-model.mjs';process.stdout.write(JSON.stringify(normalizePublicationDesign({})))"],
        cwd=ROOT, capture_output=True, text=True, timeout=15, check=True)
    return json.loads(value.stdout)


def image_data(size=(1200, 800), mode='RGB'):
    from PIL import Image
    out=io.BytesIO()
    Image.new(mode, size, '#b79665').save(out, format='PNG')
    raw=out.getvalue()
    return 'data:image/png;base64,'+base64.b64encode(raw).decode(),raw


class PublicationBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.template=design_fixture()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.original={'fields':{'price':'80000','photos':['https://images.cdn-cian.ru/example.jpg']},'titles':['保留标题'],'body':'保留正文','tags':'#保留标签'}
        self.card=local_server.save_result(self.directory,self.original)

    def tearDown(self):self.temp.cleanup()

    def test_browser_and_backend_binding_inventories_match(self):
        result=subprocess.run([resolve_node(),'--input-type=module','-e',
            "import {PUBLICATION_BINDINGS} from './web/publication-model.mjs';process.stdout.write(JSON.stringify(PUBLICATION_BINDINGS.map(row=>row.id)))"],
            cwd=ROOT,capture_output=True,text=True,timeout=15,check=True)
        self.assertEqual(set(json.loads(result.stdout)),set(publication_schema.BINDINGS))

    def test_actual_browser_v2_contract_roundtrips_and_v1_stays_unchanged(self):
        v1={'version':1,'preset':'night','background':'#17251e','intro':{'headline':'旧标题'}}
        card_assets.save_design(self.directory,v1)
        self.assertEqual(card_assets.load_design(self.directory)['template'],v1)
        saved=card_assets.save_design(self.directory,self.template)
        self.assertEqual(saved['template'],self.template)
        self.assertEqual(card_assets.load_design(self.directory)['template'],self.template)
        self.assertEqual(local_server.read_result(self.directory,self.card),self.original)
        self.assertEqual(card_assets.archive(self.directory,self.card)['attachments'],[])

    def test_actual_publication_html_passes_static_boundary_and_keeps_landscape_dark_css(self):
        data,_=image_data((64,64))
        request={'card':{'fields':{'price':'80000','area':'54','rooms':'2','metro':'Юго-Западная'},'titles':['莫斯科公寓'],'body':'已核实的房源介绍。<script>alert(1)</script>','tags':'#莫斯科租房'},'assets':{'photos':[data]}}
        rendered=subprocess.run([resolve_node(),'--input-type=module','-e',
            "import {buildPublicationHtml} from './web/publication-render.mjs';let s='';for await(const c of process.stdin)s+=c;const v=JSON.parse(s);process.stdout.write(buildPublicationHtml(v.card,{},v.assets))"],
            cwd=ROOT,input=json.dumps(request,ensure_ascii=False),capture_output=True,text=True,timeout=15,check=True).stdout
        self.assertEqual(card_assets.validate_html(rendered),rendered)
        self.assertIn('size:A4 landscape',rendered)
        self.assertIn('background:#07162b',rendered)
        self.assertIn('lang="zh-CN"',rendered)
        self.assertIn('ciliev',rendered)
        self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;script&gt;',rendered)
        created=card_assets.create_export(self.directory,self.card,rendered,'html')
        self.assertTrue(created['name'].startswith('ciliev-'))
        self.assertEqual(card_assets.export_asset(self.directory,created['id'])[0],rendered.encode())

    def test_v2_rejects_unbounded_geometry_unknown_styles_bindings_and_nonfinite_values(self):
        variants=[]
        def altered(fn):
            value=copy.deepcopy(self.template);fn(value);variants.append(value)
        altered(lambda t:t.update(script='<script>'))
        altered(lambda t:t['brand'].update(logoAssetId='https://example.com/a.png'))
        altered(lambda t:t['surfaces'].pop('intro'))
        altered(lambda t:t['surfaces']['intro']['nodes'][0].update(x=-1))
        altered(lambda t:t['surfaces']['intro']['nodes'][0].update(w=90000))
        altered(lambda t:t['surfaces']['intro']['nodes'][0].update(bind='constructor'))
        altered(lambda t:t['surfaces']['intro']['nodes'][0].update(text='x'*20001))
        altered(lambda t:t['surfaces']['intro']['nodes'][0]['style'].update(opacity=float('nan')))
        altered(lambda t:t['surfaces']['intro']['nodes'][0]['style'].update(background='url(file:///tmp/private)'))
        altered(lambda t:t['surfaces']['intro']['nodes'][0]['style'].update(transform='rotate(20deg)'))
        altered(lambda t:t['surfaces']['intro']['nodes'].append(copy.deepcopy(t['surfaces']['intro']['nodes'][0])))
        altered(lambda t:t['surfaces']['intro']['nodes'][0].update(visible='false'))
        for template in variants:
            with self.subTest(template=str(template)[:80]),self.assertRaises(ValueError):card_assets.save_design(self.directory,template)
        self.assertIsNone(card_assets.load_design(self.directory)['template'])

    def test_compare_and_swap_preserves_newer_global_design_and_returns_route_conflict(self):
        initial=card_assets.save_design(self.directory,self.template,expected_updated=None)
        newer=copy.deepcopy(self.template);newer['brand']['tagline']='已确认的新设计'
        saved=card_assets.save_design(self.directory,newer,expected_updated=initial['updated'])
        self.assertGreater(saved['updated'],initial['updated'])
        with self.assertRaises(card_assets.DesignConflict):card_assets.save_design(self.directory,self.template,expected_updated=initial['updated'])
        status,body,_,_=card_api.dispatch(self.directory,'design',{'template':self.template,'expectedUpdated':None})
        self.assertEqual(status,409);self.assertIn('другом устройстве',body['error'])
        self.assertEqual(card_assets.load_design(self.directory)['template'],newer)

    def test_private_brand_image_retains_original_and_exposes_only_bounded_clean_preview(self):
        data,original=image_data()
        created=card_assets.add_design_image(self.directory,{'data':data,'name':'头像.png','role':'avatar'})
        self.assertEqual((created['width'],created['height']),(1024,683))
        self.assertEqual(created['role'],'avatar');self.assertEqual(created['mime'],'image/png')
        self.assertNotIn('original_digest',created)
        with sqlite3.connect(self.directory/'card-assets/dossiers.sqlite3') as db:
            digest,original_digest=db.execute('SELECT digest,original_digest FROM design_images').fetchone()
        self.assertEqual(card_assets._read(self.directory,original_digest),original)
        clean=base64.b64decode(created['dataUrl'].split(',',1)[1])
        self.assertEqual(card_assets._read(self.directory,digest),clean)
        from PIL import Image
        with Image.open(io.BytesIO(clean)) as image:
            self.assertLessEqual(max(image.size),1024);self.assertNotIn('exif',image.info)
        self.assertEqual(card_assets.design_image(self.directory,created['id']),created)
        template=copy.deepcopy(self.template);template['brand']['avatarAssetId']=created['id']
        self.assertEqual(card_assets.save_design(self.directory,template)['template'],template)
        self.assertEqual(local_server.read_result(self.directory,self.card),self.original)

    def test_brand_images_reject_spoofed_or_active_data_and_missing_references(self):
        data,_=image_data((64,64))
        values=[{'data':'https://example.com/avatar.png','name':'a.png','role':'avatar'},
            {'data':'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=','name':'a.svg','role':'logo'},
            {'data':data.replace('image/png','image/jpeg'),'name':'a.jpg','role':'avatar'},
            {'data':data,'name':'../a.png','role':'avatar'},
            {'data':data,'name':'a.png','role':'unknown'}]
        for value in values:
            with self.subTest(value=str(value)[:60]),self.assertRaises(ValueError):card_assets.add_design_image(self.directory,value)
        with patch.object(card_assets,'MAX_DESIGN_IMAGE',4),self.assertRaises(ValueError):
            card_assets.add_design_image(self.directory,{'data':data,'name':'a.png','role':'avatar'})
        template=copy.deepcopy(self.template);template['brand']['logoAssetId']='a'*32
        with self.assertRaisesRegex(ValueError,'отсутствует'):card_assets.save_design(self.directory,template)

    def test_removing_attachment_cannot_remove_shared_brand_original_bytes(self):
        data,original=image_data((64,64))
        created=card_assets.add_design_image(self.directory,{'data':data,'name':'logo.png','role':'logo'})
        attachment=card_assets.add_attachment(self.directory,self.card,{'name':'copy.png','category':'other','data':base64.b64encode(original).decode()})['attachments'][0]
        card_assets.remove_attachment(self.directory,self.card,attachment['id'])
        self.assertEqual(card_assets.design_image(self.directory,created['id']),created)
        with sqlite3.connect(self.directory/'card-assets/dossiers.sqlite3') as db:
            digest=db.execute('SELECT original_digest FROM design_images').fetchone()[0]
        self.assertEqual(card_assets._read(self.directory,digest),original)

    def test_map_style_route_forwards_only_explicit_render_parameters(self):
        import listing_map
        with patch.object(listing_map,'map_image',return_value={'dataUrl':'fixture'}) as render:
            value=card_api.dispatch(self.directory,'listing-map',{'latitude':55.7,'longitude':37.5,'zoom':16,'style':'navy','pinColor':'#ef4b69'})
        self.assertEqual(value[0],200)
        render.assert_called_once_with(self.directory,55.7,37.5,16,style='navy',pin_color='#ef4b69')


class PublicationRouteAuthentication(unittest.TestCase):
    setUp=route_fixture.CardDocumentRoutes.setUp
    tearDown=route_fixture.CardDocumentRoutes.tearDown
    request=route_fixture.CardDocumentRoutes.request
    def test_design_returns_only_authenticated_personal_translation_rules(self):
        import phone_relay
        phone_relay.save_profile(self.directory,{'rules':{'Юго-Западная':'自定义西南站'},'prefs':{'contact':'private profile contact'},'data':{'style':'compact'}})
        self.assertEqual(self.request('design',auth=False)[0],401)
        status,body,_=self.request('design');self.assertEqual(status,200,body)
        result=json.loads(body)
        self.assertEqual(result['rules'],{'Юго-Западная':'自定义西南站'})
        self.assertNotIn('prefs',result);self.assertNotIn('data',result)
        self.assertNotIn('private profile contact',body.decode())

    def test_design_images_require_session_and_csrf_before_storage(self):
        data,_=image_data((64,64));payload={'data':data,'name':'头像.png','role':'avatar'}
        self.assertEqual(self.request('design-images',payload,auth=False)[0],401)
        self.assertEqual(self.request('design-images',payload,csrf=False)[0],403)
        desktop='http://127.0.0.1:'+str(self.desktop.server_port)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(desktop+'/api/local/design-images/'+'a'*32)
        self.assertEqual(denied.exception.code,403)
        self.assertFalse((self.directory/'card-assets').exists())
        status,body,_=self.request('design-images',payload);self.assertEqual(status,200,body)
        asset=json.loads(body)
        self.assertEqual(self.request('design-images/'+asset['id'],auth=False)[0],401)
        status,body,_=self.request('design-images/'+asset['id']);self.assertEqual(status,200,body)
        self.assertEqual(json.loads(body)['dataUrl'],asset['dataUrl'])
        template=design_fixture();template['brand']['avatarAssetId']=asset['id']
        status,body,_=self.request('design',{'template':template,'expectedUpdated':None});self.assertEqual(status,200,body)
        self.assertEqual(self.request('design',{'template':template,'expectedUpdated':None})[0],409)


if __name__=='__main__':unittest.main()
