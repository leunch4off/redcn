"""Explicit, bounded road-network routing; never fabricates transit itineraries.

FOSSGIS Valhalla is a public demonstration service, not a production SLA. Only
an operator's deliberate request sends the two selected coordinates. No names,
addresses, cookies, photographs, credentials or listing identifiers are sent.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
from zoneinfo import ZoneInfo
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode
import urllib.error
import urllib.request

ENDPOINT = 'https://valhalla1.openstreetmap.de/route'
CLIENT_ID = 'github.com/leunch4off/redcn'
MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 12
TTL_SECONDS = 600
DATABASE = 'route-provider.sqlite3'
_LOCK = threading.Lock()
_LAST_CALL = 0.0
_POINT_KEYS = {'latitude', 'longitude', 'name', 'kind', 'availability', 'checkedAt', 'sourceUrl'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Координаты и числа маршрута должны быть конечными числами в допустимом диапазоне')
    return value


def _point(value, require_entrance=False):
    if not isinstance(value, dict) or set(value)-_POINT_KEYS:
        raise ValueError('Неверные поля выбранной точки маршрута')
    point = {'latitude':_number(value.get('latitude'), -90, 90), 'longitude':_number(value.get('longitude'), -180, 180)}
    point['name'] = value.get('name', '')
    if not isinstance(point['name'], str) or len(point['name'])>160 or any(ord(c)<32 for c in point['name']):
        raise ValueError('Название точки: до 160 символов без управляющих знаков')
    point['kind'] = value.get('kind', 'point')
    if point['kind'] not in ('point', 'building-entrance', 'metro-exit'):
        raise ValueError('Выберите точку, вход дома или выход метро')
    if require_entrance and point['kind']=='point':
        raise ValueError('Для расчёта по входам нужны конкретные входы, а не центры зданий')
    point['availability'] = value.get('availability', 'unknown')
    if point['availability'] not in ('available', 'closed', 'unknown'):
        raise ValueError('Неизвестная доступность входа')
    point['checkedAt'], point['sourceUrl'] = value.get('checkedAt', ''), value.get('sourceUrl', '')
    if not isinstance(point['checkedAt'], str) or not isinstance(point['sourceUrl'], str) or len(point['sourceUrl'])>1200:
        raise ValueError('Неверные дата или источник входа')
    if point['kind']!='point':
        if point['availability']!='available':
            raise ValueError('Закрытый или неподтверждённый вход не используется для расчёта')
        try:
            day=datetime.strptime(point['checkedAt'], '%Y-%m-%d').date()
        except ValueError as error:
            raise ValueError('Для входа нужны дата проверки и HTTPS-источник') from error
        age=(datetime.now(ZoneInfo('Europe/Moscow')).date()-day).days
        if not 0<=age<=30 or not re.fullmatch(r'https://[^\s@<>]+',point['sourceUrl']):
            raise ValueError('Вход должен быть проверен за последние 30 дней, с HTTPS-источником')
    return point


def normalize_request(data):
    if not isinstance(data, dict) or set(data)-{'origin','destination','mode','useEntrances','shareCoordinates'}:
        raise ValueError('Неверный запрос расчёта маршрута')
    if data.get('shareCoordinates') is not True:
        raise ValueError('Расчёт отправляет только выбранные координаты в Valhalla; подтвердите отправку')
    if type(data.get('useEntrances',False)) is not bool:
        raise ValueError('Расчёт по входам задаётся переключателем')
    mode=data.get('mode')
    if mode not in ('walk','car'):
        raise ValueError('Этот провайдер рассчитывает только пешие и автомобильные маршруты. Автобусы и пересадки не подменяются автомобильным профилем.')
    origin=_point(data.get('origin'),data.get('useEntrances',False))
    destination=_point(data.get('destination'),data.get('useEntrances',False))
    if origin['latitude']==destination['latitude'] and origin['longitude']==destination['longitude']:
        raise ValueError('Начало и конец маршрута совпадают')
    return {'origin':origin,'destination':destination,'mode':mode,'useEntrances':data.get('useEntrances',False)}


def provider_payload(request):
    # Names and source URLs stay private. Radius prevents a building-centre snap
    # from silently selecting an unrelated road hundreds of metres away.
    return {'locations':[{'lat':request[key]['latitude'],'lon':request[key]['longitude'],'type':'break','radius':30,'search_cutoff':100} for key in ('origin','destination')],
            'costing':'pedestrian' if request['mode']=='walk' else 'auto',
            'units':'kilometers','directions_options':{'language':'ru-RU'},'alternates':0}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _fetch(payload):
    # This function runs in an owned subprocess with a hard parent deadline, so
    # DNS and trickling responses cannot hold a server thread indefinitely.
    addresses=list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo('valhalla1.openstreetmap.de',443,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Маршрутный сервис разрешился в непубличный адрес')
    from connection_diagnostics import _PinnedHTTPS
    try:
        import certifi
        context=ssl.create_default_context(cafile=certifi.where())
    except ImportError:context=ssl.create_default_context()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_PinnedHTTPS(addresses,context),_NoRedirect())
    url=ENDPOINT+'?'+urlencode({'json':_json(payload)})
    request=urllib.request.Request(url,headers={'User-Agent':'redcn-route-provider/1.0','X-Client-Id':CLIENT_ID,'Accept':'application/json','Accept-Encoding':'identity'})
    try:
        with opener.open(request,timeout=TIMEOUT_SECONDS-1) as response:
            if response.headers.get_content_type()!='application/json':raise ValueError('Маршрутный сервис вернул другой формат')
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise ValueError('Ответ маршрута превышает 2 МБ')
    except urllib.error.HTTPError as error:
        if error.code==429:raise ValueError('Лимит публичного маршрутного сервера: повторите позднее') from None
        raise ValueError('Маршрутный сервис вернул HTTP '+str(error.code)) from None
    return json.loads(raw)


def _bounded_fetch(payload):
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        try:output,_=process.communicate(_json(payload).encode(),timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill();process.communicate(timeout=2)
            raise TimeoutError('Маршрутный сервис не ответил за 12 секунд') from None
        if len(output)>MAX_BYTES:raise ValueError('Служебный ответ маршрута слишком большой')
        value=json.loads(output)
        if value.get('error'):raise ValueError(str(value['error'])[:300])
        if process.returncode:raise ValueError('Процесс маршрута завершился с ошибкой')
        return value['response']
    finally:
        if process.poll() is None:process.kill();process.communicate(timeout=2)


def _shape_endpoints(value):
    if not isinstance(value,str) or not value or len(value)>500000:raise ValueError('Сервис не вернул ограниченную геометрию маршрута')
    index=0;lat=lon=0;first=last=None
    while index<len(value):
        pair=[]
        for _ in range(2):
            shift=result=0
            while True:
                if index>=len(value) or shift>30:raise ValueError('Неверная геометрия маршрута')
                byte=ord(value[index])-63;index+=1
                if not 0<=byte<=63:raise ValueError('Неверная геометрия маршрута')
                result|=(byte&31)<<shift;shift+=5
                if byte<32:break
            pair.append(~(result>>1) if result&1 else result>>1)
        lat+=pair[0];lon+=pair[1];last={'latitude':_number(lat/1000000,-90,90),'longitude':_number(lon/1000000,-180,180)}
        if first is None:first=last
    if first is None or last is None:raise ValueError('Пустая геометрия маршрута')
    return first,last


def _meters(a,b):
    r=math.pi/180;dlat=(a['latitude']-b['latitude'])*r;dlon=(a['longitude']-b['longitude'])*r
    x=math.sin(dlat/2)**2+math.cos(a['latitude']*r)*math.cos(b['latitude']*r)*math.sin(dlon/2)**2
    return 6371008.8*2*math.asin(math.sqrt(min(1,max(0,x))))


def _normal_response(value,request,now):
    if not isinstance(value,dict) or not isinstance(value.get('trip'),dict):raise ValueError('Сервис не вернул маршрут')
    trip=value['trip'];summary=trip.get('summary')
    if type(trip.get('status')) is not int or trip['status']!=0 or trip.get('units')!='kilometers':raise ValueError('Сервис не подтвердил маршрут в километрах')
    if not isinstance(summary,dict):raise ValueError('Сервис не вернул время и длину маршрута')
    seconds=_number(summary.get('time'),0,172800);distance=_number(summary.get('length'),0,2000)
    locations=trip.get('locations')
    if not isinstance(locations,list) or len(locations)!=2:raise ValueError('Сервис не подтвердил обе точки маршрута')
    legs=trip.get('legs');
    if not isinstance(legs,list) or not legs or len(legs)>8 or any(not isinstance(leg,dict) for leg in legs):raise ValueError('Сервис не подтвердил участки маршрута')
    first=_shape_endpoints(legs[0].get('shape'))[0];last=_shape_endpoints(legs[-1].get('shape'))[1]
    snap_meters=[round(_meters(first,request['origin']),2),round(_meters(last,request['destination']),2)]
    if request['useEntrances'] and any(distance>30 for distance in snap_meters):raise ValueError('Дорожная сеть привязала выбранный вход дальше 30 м. Проверьте доступный вход; центр здания не подставляется.')
    snap=[]
    for index,key in enumerate(('origin','destination')):
        row=locations[index]
        if not isinstance(row,dict):raise ValueError('Неверная точка в ответе сервиса')
        # The response must echo requested locations; correlated network edges
        # are an estimate, not proof of physically open doors or accessibility.
        if abs(_number(row.get('lat'),-90,90)-request[key]['latitude'])>.00001 or abs(_number(row.get('lon'),-180,180)-request[key]['longitude'])>.00001:
            raise ValueError('Сервис вернул другой порядок или координаты точек')
        correlation=row.get('correlated_lat'),row.get('correlated_lon')
        snap.append({'latitude':correlation[0],'longitude':correlation[1]} if all(type(x) in (int,float) and math.isfinite(x) for x in correlation) else None)
    calculated=datetime.fromtimestamp(now,timezone.utc).isoformat().replace('+00:00','Z')
    expires=datetime.fromtimestamp(now+TTL_SECONDS,timezone.utc).isoformat().replace('+00:00','Z')
    version=value.get('id','')
    return {'provider':'valhalla-fossgis-demo','calculatedAt':calculated,'expiresAt':expires,'checkedAt':datetime.fromtimestamp(now,ZoneInfo('Europe/Moscow')).date().isoformat(),
            'mode':request['mode'],'durationSeconds':seconds,'distanceKm':distance,
            'time':str(max(1,math.ceil(seconds/60))),'routeDistanceKm':distance,
            'sourceUrl':ENDPOINT+'?'+urlencode({'json':_json(provider_payload(request))}),
            'origin':request['origin'],'destination':request['destination'],'useEntrances':request['useEntrances'],
            'correlatedPoints':snap,'networkEndpointSnapMeters':snap_meters,'attribution':'© OpenStreetMap contributors · Valhalla / FOSSGIS e.V.',
            'travelTimeKind':'network-estimate','transitSupported':False,'liveTrafficConfirmed':False,
            'responseSha256':hashlib.sha256(_json(value).encode()).hexdigest(),
            'warnings':['Время рассчитано по дорожной сети; фактическая поездка, пробки и доступность входов могут отличаться.','Публичный демонстрационный сервис без SLA; расчёт не содержит расписаний автобусов и пересадок.']}


@contextmanager
def _db(directory):
    root=Path(directory);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=root/DATABASE
    if path.is_symlink():raise ValueError('Кеш маршрутов не должен быть ссылкой')
    db=sqlite3.connect(path,timeout=5);path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS routes (key TEXT PRIMARY KEY,expires REAL NOT NULL,payload TEXT NOT NULL)')
    try:
        with db:yield db
    finally:db.close()


def calculate(directory,data,*,fetch=None,now=None):
    request=normalize_request(data);now=time.time() if now is None else now
    key=hashlib.sha256(_json(request).encode()).hexdigest()
    with _db(directory) as db:
        row=db.execute('SELECT expires,payload FROM routes WHERE key=?',(key,)).fetchone()
        if row and row[0]>now:return {**json.loads(row[1]),'cached':True}
    if not _LOCK.acquire(False):raise ValueError('Другой расчёт маршрута ещё выполняется')
    global _LAST_CALL
    try:
        if fetch is None and time.monotonic()-_LAST_CALL<5:raise ValueError('Между запросами публичного сервиса должно пройти не менее пяти секунд')
        _LAST_CALL=time.monotonic()
        response=(fetch or _bounded_fetch)(provider_payload(request))
        result=_normal_response(response,request,now)
        with _db(directory) as db:
            db.execute('DELETE FROM routes WHERE expires<=?',(now,))
            db.execute('INSERT OR REPLACE INTO routes VALUES(?,?,?)',(key,now+TTL_SECONDS,_json(result)))
            db.execute('DELETE FROM routes WHERE key NOT IN (SELECT key FROM routes ORDER BY expires DESC LIMIT 100)')
        return {**result,'cached':False}
    finally:_LOCK.release()


def dispatch(directory,path,data=None):
    if path=='route-provider' and data is None:
        return 200,{'provider':'valhalla-fossgis-demo','endpoint':ENDPOINT,'modes':['walk','car'],'transitSupported':False,'automaticRequests':False,'keyRequired':False,'ttlSeconds':TTL_SECONDS,'timeoutSeconds':TIMEOUT_SECONDS,'docs':'https://valhalla.github.io/valhalla/start/introduction/','attribution':'© OpenStreetMap contributors · Valhalla / FOSSGIS e.V.'},'application/json; charset=utf-8',{}
    if path=='route-provider/calculate' and data is not None:
        return 200,calculate(directory,data),'application/json; charset=utf-8',{}
    return None


if __name__=='__main__':
    try:
        if sys.argv[1:]!=['--worker']:raise ValueError('Unknown invocation')
        raw=sys.stdin.read(8001)
        if len(raw)>8000:raise ValueError('Route request too large')
        print(_json({'response':_fetch(json.loads(raw))}),flush=True)
    except Exception as error:
        print(_json({'error':str(error)[:300]}),flush=True);raise SystemExit(1)
