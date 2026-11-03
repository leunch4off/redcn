import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'cli'))
import route_provider as provider

def shape(points):
 result='';previous=[0,0]
 for point in points:
  for index,key in enumerate(('latitude','longitude')):
   current=round(point[key]*1000000);delta=current-previous[index];previous[index]=current;encoded=~(delta<<1) if delta<0 else delta<<1
   while encoded>=32:result+=chr((32|(encoded&31))+63);encoded>>=5
   result+=chr(encoded+63)
 return result

class RouteProviderTests(unittest.TestCase):
 def request(self):return {'origin':{'latitude':55.699876,'longitude':37.529543,'name':'A private label never sent'},'destination':{'latitude':55.701111111,'longitude':37.5425,'name':'B'},'mode':'walk','shareCoordinates':True}
 def response(self,request=None):
  r=request or self.request();return {'trip':{'status':0,'units':'kilometers','summary':{'time':720.5,'length':.864},'locations':[{'lat':r[k]['latitude'],'lon':r[k]['longitude']} for k in ('origin','destination')],'legs':[{'shape':shape([r['origin'],r['destination']])}]}}
 def test_actual_provider_contract_separates_network_estimate_from_transit_and_live_traffic(self):
  with tempfile.TemporaryDirectory() as d:
   out=provider.calculate(d,self.request(),fetch=lambda p:self.response(),now=1793550000)
   self.assertEqual(out['durationSeconds'],720.5);self.assertEqual(out['time'],'13')
   self.assertEqual(out['calculatedAt'],'2026-11-01T16:20:00Z');self.assertFalse(out['transitSupported']);self.assertFalse(out['liveTrafficConfirmed'])
   self.assertEqual(out['travelTimeKind'],'network-estimate');self.assertIn('OpenStreetMap',out['attribution'])
 def test_only_explicit_coordinate_sharing_and_supported_modes(self):
  for patch in ({'shareCoordinates':False},{'shareCoordinates':None},{'mode':'public'},{'mode':'bus'},{'mode':'metro'},{'endpoint':'https://evil.invalid'}):
   with self.subTest(patch=patch),self.assertRaises(ValueError):provider.normalize_request({**self.request(),**patch})
 def test_private_labels_source_urls_and_unrelated_fields_never_leave_request(self):
  r=provider.normalize_request(self.request());data=provider.provider_payload(r)
  self.assertNotIn('private',json.dumps(data));self.assertNotIn('name',json.dumps(data));self.assertEqual(data['locations'][0]['radius'],30)
  self.assertEqual(data['costing'],'pedestrian');self.assertEqual(provider.provider_payload({**r,'mode':'car'})['costing'],'auto')
 def test_nonfinite_bool_unknown_coordinates_and_identical_points_rejected(self):
  for point in ({'latitude':True,'longitude':37},{'latitude':float('nan'),'longitude':37},{'latitude':55,'longitude':181},{'latitude':'55','longitude':37},{'latitude':55,'longitude':37,'url':'https://evil.invalid'}):
   with self.subTest(point=point),self.assertRaises(ValueError):provider.normalize_request({**self.request(),'origin':point})
  r=self.request();r['destination']=r['origin']
  with self.assertRaises(ValueError):provider.normalize_request(r)
 def test_entrances_are_selected_exactly_and_closed_unknown_or_centroids_rejected(self):
  r=self.request();r['useEntrances']=True
  with self.assertRaises(ValueError):provider.normalize_request(r)
  today=time.strftime('%Y-%m-%d',time.gmtime())
  for k in ('origin','destination'):r[k].update(kind='metro-exit' if k=='origin' else 'building-entrance',availability='available',checkedAt=today,sourceUrl='https://example.org/verified-door')
  out=provider.normalize_request(r);payload=provider.provider_payload(out)
  self.assertEqual(payload['locations'][0]['lat'],r['origin']['latitude']);self.assertTrue(out['useEntrances'])
  for bad in ({'availability':'closed'},{'availability':'unknown'},{'checkedAt':'2000-01-01'},{'sourceUrl':'http://example.org'},{'checkedAt':'2099-01-01'}):
   broken=copy.deepcopy(r);broken['destination'].update(bad)
   with self.subTest(bad=bad),self.assertRaises(ValueError):provider.normalize_request(broken)
 def test_provider_mismatches_and_errors_do_not_become_successful_route(self):
  for change in ({'status':1},{'units':'miles'},{'summary':{'time':float('nan'),'length':1}},{'locations':[]},{'locations':[{'lat':55,'lon':37},{'lat':55,'lon':37}]}):
   response=self.response();response['trip'].update(change)
   with self.subTest(change=change),tempfile.TemporaryDirectory() as d,self.assertRaises(ValueError):provider.calculate(d,self.request(),fetch=lambda p:response)
 def test_malformed_provider_shapes_are_validation_errors_and_do_not_poison_cache(self):
  for change in ({'status':False},{'summary':None},{'summary':[]},{'legs':[None]},{'legs':[{}]}):
   response=self.response();response['trip'].update(change)
   with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
    with self.assertRaises(ValueError):provider.calculate(d,self.request(),fetch=lambda p:response)
    recovered=provider.calculate(d,self.request(),fetch=lambda p:self.response())
    self.assertFalse(recovered['cached']);self.assertEqual(recovered['durationSeconds'],720.5)
 def test_cache_preserves_calculation_time_expires_and_isolated_by_workspace_and_mode(self):
  calls=[]
  def fetch(p):calls.append(p);return self.response()
  with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory() as other:
   a=provider.calculate(d,self.request(),fetch=fetch,now=1000)
   b=provider.calculate(d,self.request(),fetch=fetch,now=1100)
   self.assertTrue(b['cached']);self.assertEqual(a['calculatedAt'],b['calculatedAt']);self.assertEqual(len(calls),1)
   provider.calculate(d,self.request(),fetch=fetch,now=1601);self.assertEqual(len(calls),2)
   provider.calculate(other,self.request(),fetch=fetch,now=1100);self.assertEqual(len(calls),3)
   provider.calculate(d,{**self.request(),'mode':'car'},fetch=fetch,now=1100);self.assertEqual(len(calls),4)
 def test_distant_network_snap_cannot_claim_selected_door_routing(self):
  r=self.request();r['useEntrances']=True
  for key in ('origin','destination'):r[key].update(kind='building-entrance',availability='available',checkedAt=time.strftime('%Y-%m-%d',time.gmtime()),sourceUrl='https://example.org/door')
  response=self.response(r);far={**r['origin'],'latitude':r['origin']['latitude']+.01};response['trip']['legs']=[{'shape':shape([far,r['destination']])}]
  with tempfile.TemporaryDirectory() as d,self.assertRaisesRegex(ValueError,'дальше 30'):provider.calculate(d,r,fetch=lambda p:response)

 def test_catalog_is_non_networking_and_readonly(self):
  with patch.object(provider,'_bounded_fetch',side_effect=AssertionError('network')):
   value=provider.dispatch('/unused','route-provider')[1];self.assertFalse(value['automaticRequests']);self.assertFalse(value['keyRequired']);self.assertEqual(value['modes'],['walk','car'])
  self.assertIsNone(provider.dispatch('/unused','unrelated'))
 def test_worker_timeout_cleans_owned_process(self):
  import subprocess
  fake=unittest.mock.Mock();fake.communicate.side_effect=[subprocess.TimeoutExpired('route',12),(b'',b'')];fake.poll.return_value=1
  with patch.object(provider.subprocess,'Popen',return_value=fake),self.assertRaises(TimeoutError):provider._bounded_fetch({})
  fake.kill.assert_called_once()

if __name__=='__main__':unittest.main()
