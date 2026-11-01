#!/usr/bin/env python3
"""One real request between two public catalogue points; no apartment data."""
import argparse,hashlib,json,sys,tempfile,time,urllib.request
from pathlib import Path
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'cli'))
import route_provider
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--transport',choices=('direct','system'),default='direct');a=p.parse_args()
request={'origin':{'latitude':55.699876,'longitude':37.529543,'name':'MSU Physics published point'},'destination':{'latitude':55.701111111,'longitude':37.5425,'name':'MSU Sternberg published point'},'mode':'walk','shareCoordinates':True}
def system_fetch(payload):
 url=route_provider.ENDPOINT+'?'+urlencode({'json':json.dumps(payload,separators=(',',':'))})
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'redcn-route-research/1.0','X-Client-Id':route_provider.CLIENT_ID,'Accept':'application/json'}),timeout=10) as response:
  raw=response.read(route_provider.MAX_BYTES+1)
  if len(raw)>route_provider.MAX_BYTES:raise ValueError('Response too large')
  return json.loads(raw)
start=time.monotonic()
report={'request':request,'transport':a.transport,'publicCataloguePointsOnly':True,'entranceAcceptance':False,'providerScriptSha256':hashlib.sha256((ROOT/'cli/route_provider.py').read_bytes()).hexdigest(),'pilotScriptSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
with tempfile.TemporaryDirectory(prefix='redcn-route-provider-public-pilot-') as cache:
 try:report.update(status='measured',result=route_provider.calculate(cache,request,**({'fetch':system_fetch} if a.transport=='system' else {})))
 except Exception as error:report.update(status='failed',errorType=type(error).__name__,error=str(error)[:400])
report.update(elapsedSeconds=round(time.monotonic()-start,3),observedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),limits=['Public campus points are published building points, not verified doors.','Route duration is a provider network estimate, not a physically measured journey.','System transport is for this executor network only; deployed code uses bounded direct/pinned HTTPS.'])
out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False));raise SystemExit(0 if report['status']=='measured' else 2)
