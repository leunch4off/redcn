"""One owned listing HTTP request worker, bounded by its parent's deadline.

This is not a browser/controller and cannot access a saved Chrome profile.
Only a validated public CIAN/Avito URL and a small time budget are accepted.
"""
import base64
import json
import ssl
import sys
import urllib.error

import redcn


def main():
    payload=json.loads(sys.stdin.read(32768))
    timeout=payload.get('timeout',30)
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 0<timeout<=60:raise ValueError('Неверный срок HTTP-загрузки')
    url=payload.get('url')
    if not isinstance(url,str) or len(url)>8192:raise ValueError('Неверная ссылка объявления')
    attempts=0
    def report(_message,**fields):
        nonlocal attempts
        attempts=fields.get('httpAttempt',attempts)
        print(json.dumps({'event':'attempt','attempt':attempts,'reason':fields.get('httpReason','')}),file=sys.stderr,flush=True)
    try:
        content,mime=redcn._fetch_bytes_direct(url,timeout=timeout,status=report)
        result={'ok':True,'content':base64.b64encode(content).decode('ascii'),'contentType':mime,'attempts':attempts}
    except urllib.error.HTTPError as error:
        result={'ok':False,'error':{'kind':'http','status':error.code,'message':'Источник вернул HTTP '+str(error.code)},'attempts':attempts}
    except (OSError,urllib.error.URLError) as error:
        reason=getattr(error,'reason',error)
        kind='tls' if isinstance(reason,ssl.SSLError) else 'timeout' if isinstance(reason,TimeoutError) else 'network'
        text='Ошибка проверки сертификата HTTPS. Небезопасное подключение не выполняется.' if kind=='tls' else 'HTTP-загрузка не ответила в отведённое время.' if kind=='timeout' else 'Сетевая ошибка HTTP-загрузки. Проверьте связь Mac с источником.'
        result={'ok':False,'error':{'kind':kind,'message':text},'attempts':attempts}
    except (ValueError,TypeError) as error:
        result={'ok':False,'error':{'kind':'validation','message':str(error)[:800]},'attempts':attempts}
    sys.stdout.write(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except (ValueError,TypeError,AttributeError):
        sys.stdout.write(json.dumps({'ok':False,'error':{'kind':'validation','message':'Неверный запрос HTTP-загрузчика'}}))
