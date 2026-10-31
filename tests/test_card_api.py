"""Dossier and export routes must keep the same auth boundary as card photos."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay


class CardDocumentRoutes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.desktop=local_server.create_server(self.directory,0)
        self.phone=phone_relay.create_phone_server(self.directory,self.desktop,0,'127.0.0.1',hosts={'127.0.0.1'})
        self.threads=[]
        for server in (self.desktop,self.phone):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.threads.append(thread)
        self.base='http://127.0.0.1:'+str(self.phone.server_port)
        code,_=self.phone.pairing.issue()
        status,body,headers=self.request('pair',{'code':code},auth=False)
        self.cookie=headers['Set-Cookie'].split(';')[0];self.csrf=json.loads(body)['csrfToken']
        self.card=local_server.save_result(self.directory,{'fields':{'price':'80000'},'titles':['房源'],'body':'Description','tags':''})

    def tearDown(self):
        for server in (self.desktop,self.phone):server.shutdown();server.server_close()
        for thread in self.threads:thread.join()
        self.temp.cleanup()

    def request(self,path,data=None,auth=True,csrf=True):
        headers={}
        if auth:headers['Cookie']=self.cookie
        if data is not None:
            headers['Content-Type']='application/json'
            if auth and csrf:headers['X-Phone-CSRF']=self.csrf
        request=urllib.request.Request(self.base+'/api/phone/'+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(request,timeout=10) as response:return response.status,response.read(),response.headers
        except urllib.error.HTTPError as error:return error.code,error.read(),error.headers

    def test_pairing_boundary_csrf_binary_integrity_and_dossier_persistence(self):
        path='card-archive/'+self.card
        self.assertEqual(self.request(path,auth=False)[0],401)
        self.assertEqual(self.request(path,{'action':'update','status':'rented','notes':'Сдана 30 октября'},csrf=False)[0],403)
        status,body,_=self.request(path,{'action':'update','status':'rented','notes':'Сдана 30 октября'})
        self.assertEqual(status,200,body);self.assertEqual(json.loads(body)['notes'],'Сдана 30 октября')
        raw=b'private lidar point cloud sample'
        status,body,_=self.request(path,{'action':'attach','name':'План квартиры.obj','category':'lidar','data':base64.b64encode(raw).decode()})
        self.assertEqual(status,200,body);item=json.loads(body)['attachments'][0]
        binary_path=item['url'].removeprefix('/api/phone/')
        self.assertEqual(self.request(binary_path,auth=False)[0],401)
        status,body,headers=self.request(binary_path)
        self.assertEqual(status,200);self.assertEqual(body,raw);self.assertIn('attachment;',headers['Content-Disposition']);self.assertIn("filename*=UTF-8''",headers['Content-Disposition'])
        self.assertEqual(json.loads(self.request(path)[1])['status'],'rented')
        self.assertEqual(local_server.read_result(self.directory,self.card)['body'],'Description')

    def test_reusable_design_html_export_phone_download_and_no_private_inclusion(self):
        design={'version':1,'preset':'minimal','accent':'#35563f','paper':'A4'}
        status,body,_=self.request('design',{'template':design});self.assertEqual(status,200,body)
        self.assertEqual(json.loads(self.request('design')[1])['template'],design)
        html='<!doctype html><html><head><meta charset="utf-8"></head><body><h1>中文 · квартира</h1></body></html>'
        status,body,_=self.request('card-export',{'result_id':self.card,'kind':'html','html':html})
        self.assertEqual(status,200,body);asset=json.loads(body)
        self.assertEqual(self.request(asset['url'].removeprefix('/api/phone/'))[1].decode(),html)
        self.assertEqual(self.request('exports/'+asset['id'],auth=False)[0],401)
        listing=json.loads(self.request('card-exports/'+self.card)[1]);self.assertEqual(listing['exports'][0]['id'],asset['id'])
        self.assertEqual(self.request('card-export',{'result_id':self.card,'kind':'html','html':html.replace('</body>','<script>alert(1)</script></body>')})[0],400)

    def test_desktop_credentials_are_injected_only_into_top_level_document(self):
        with urllib.request.urlopen('http://127.0.0.1:'+str(self.desktop.server_port)+'/',timeout=10) as response:
            page=response.read().decode()
        self.assertEqual(page.count('<meta name="redcn-token"'),1)
        self.assertEqual(page.count('<base href="/">'),1)


if __name__=='__main__':unittest.main()
