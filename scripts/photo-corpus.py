#!/usr/bin/env python3
"""Explicit private photo corpus export and separate calibration/test reports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import photo_training
import photo_calibration


def read(path):
    path=Path(path); info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=20_000_000:
        raise ValueError('Нужен обычный JSON-файл до 20 МБ')
    return json.loads(path.read_text())


def write_new(path, value):
    path=Path(path)
    if any(p.is_symlink() for p in (path.parent,*path.parents)):
        raise ValueError('Каталог вывода не должен быть ссылкой')
    descriptor=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor,'w') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def export(args):
    import photo_store
    manifest=photo_training.export_manifest(Path(args.cache).expanduser().resolve(),args.card_id)
    output=Path(args.output).expanduser().absolute()
    if any(p.is_symlink() for p in (output,*output.parents)):
        raise ValueError('Каталог вывода не должен быть ссылкой')
    output.mkdir(mode=0o700)
    (output/'INCOMPLETE').write_text('Export is incomplete until manifest.json is written.\n')
    images=output/'images';images.mkdir(mode=0o700)
    count=0
    for rows in manifest['splits'].values():
        for row in rows:
            content,_,_=photo_store.asset(Path(args.cache).expanduser().resolve(),row['sha256'])
            if hashlib.sha256(content).hexdigest()!=row['sha256']:raise ValueError('Изменился оригинал')
            destination=images/row['sha256']
            descriptor=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(descriptor,'wb') as stream:stream.write(content)
            row.update(path='images/'+row['sha256'],status='downloaded');count+=1
    manifest['sha256']=photo_training.content_sha({k:v for k,v in manifest.items() if k!='sha256'})
    write_new(output/'manifest.json',manifest);(output/'INCOMPLETE').unlink()
    return {'kind':'private-photo-corpus-export','imageCount':count,
            'splits':{key:len(rows) for key,rows in manifest['splits'].items()},
            'excluded':manifest['excluded'],'manifestSha256':manifest['sha256'],
            'automaticAcceptance':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('export');p.add_argument('--cache',required=True);p.add_argument('--card-id',action='append',required=True);p.add_argument('--output',required=True)
    for name in ('freeze','evaluate'):
        p=sub.add_parser(name);p.add_argument('--manifest',required=True);p.add_argument('--predictions',required=True);p.add_argument('--model-sha',required=True);p.add_argument('--output',required=True)
        if name=='evaluate':p.add_argument('--frozen',required=True);p.add_argument('--diagnostic',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    try:
        if args.action=='export':result=export(args)
        else:
            corpus,predictions=read(args.manifest),read(args.predictions)
            if args.action=='freeze':result=photo_calibration.freeze(corpus,predictions,model_sha=args.model_sha)
            else:
                frozen=read(args.frozen)
                result=photo_calibration.evaluate_test(corpus,predictions,frozen,model_sha=args.model_sha)
                # Bind one heldout use to the exact corpus/model/frozen thresholds.
                # Subsequent uses require explicit diagnostic mode and remain so.
                marker=Path(args.frozen).absolute().parent/('.test-used-'+photo_calibration.digest([result['corpusSha256'],result['modelSha256'],result['calibrationSha256']])+'.json')
                if marker.exists() and not args.diagnostic:raise ValueError('Этот отложенный набор уже раскрыт. Новый запуск допускается только как --diagnostic.')
                result['diagnosticOnly']=bool(args.diagnostic or marker.exists())
                if not marker.exists():write_new(marker,{'corpusSha256':result['corpusSha256'],'modelSha256':result['modelSha256'],'calibrationSha256':result['calibrationSha256']})
            write_new(args.output,result)
            result={'kind':result['kind'],'classes':len(result['classes']),
                    'statisticallyPassingClasses':result.get('statisticallyPassingClasses',[]),
                    'diagnosticOnly':result.get('diagnosticOnly',False),'automaticAcceptance':False}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,OSError,KeyError,TypeError) as error:
        # Never expose filenames, card IDs, raw labels or model output in errors.
        print(json.dumps({'ok':False,'errorType':type(error).__name__,'action':args.action,
                          'message':str(error) if isinstance(error,ValueError) else 'Проверьте файлы, права и новый путь вывода.'},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
