#!/usr/bin/env python3
"""Compare supplied official Moscow name exports without changing the catalogue.

The manifest identifies each source, publisher's date, scope and exact bytes.
Name presence never proves that duplicate stops/platforms are the same object.
A supplied export is not automatically certified as the latest complete registry.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('geo_importer',ROOT/'scripts/update-moscow-geo.py')
geo=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(geo)
OFFICIAL_HOSTS={'data.mos.ru','data-new.mos.ru','api.data.mos.ru','transport.mos.ru','mosmetro.ru','www.mosmetro.ru','fias.nalog.ru','fias-public-service.nalog.ru'}

def dated(value):
    if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value or date.fromisoformat(value)>date.today():
        raise ValueError('A publisher date (YYYY-MM-DD, not in the future) is required')
    return value

def compare(catalog, exports, *, manifest_root=None):
    if not isinstance(exports,list) or not exports or len(exports)>3:raise ValueError('Provide one to three separately identified exports')
    reports=[]; seen=set()
    for source in exports:
        if not isinstance(source,dict) or source.get('kind') not in ('metro','street','stop') or source['kind'] in seen:raise ValueError('Each geographic kind must have its own export')
        kind=source['kind'];seen.add(kind);url=urlsplit(source.get('url',''))
        if url.scheme!='https' or url.hostname not in OFFICIAL_HOSTS or url.username or url.password or url.fragment:raise ValueError('Official export must have an explicit primary HTTPS source')
        as_of=dated(source.get('asOf'));geo.validate_official_export_metadata(source['url'],as_of)
        if not isinstance(source.get('scope'),str) or not source['scope'].strip():raise ValueError('Describe the actual registry coverage')
        path=Path(source.get('file',''));path=path if path.is_absolute() else Path(manifest_root or '.')/path
        if path.is_symlink() or not path.is_file() or path.stat().st_size>50_000_000:raise ValueError('Use a regular local export of at most 50 MB')
        records,raw=geo.official_records(path,kind);digest=hashlib.sha256(raw).hexdigest()
        if not isinstance(source.get('sha256'),str) or source['sha256']!=digest:raise ValueError('Export SHA256 must match the manifest')
        expected=source.get('expectedRecords')
        if expected is not None and (type(expected) is not int or expected!=len(records)):raise ValueError('Recognized record count does not match the supplied publisher count')
        existing={geo.norm(row['ru']) for row in catalog['records'] if row['kind']==kind}
        grouped={}
        for row in records:grouped.setdefault(geo.norm(row['ru']),[]).append(row)
        missing=[{'ru':rows[0]['ru'],'recordIds':[row['id'] for row in rows]} for name,rows in grouped.items() if name not in existing]
        reports.append({'kind':kind,'sourceUrl':source['url'],'asOf':as_of,'scope':source['scope'],'sha256':digest,'recognizedRecords':len(records),'uniqueNames':len(grouped),'catalogueNames':len(existing),'matchedNames':sum(name in existing for name in grouped),'missingNames':missing,'matchBasis':'normalized-name-presence-only','objectIdentityVerified':False,'latestCompleteRegistryIndependentlyVerified':False,'publisherCountSupplied':expected})
    return {'version':1,'checkedAt':datetime.now(timezone.utc).isoformat(),'mode':'read-only-supplied-primary-exports','catalogueMutated':False,'reports':reports,'allThreeKindsCompared':seen=={'metro','street','stop'},'limitations':['Input files and publisher metadata are supplied explicitly; the tool does not bypass protected API access.','Name matching does not validate duplicate stops, platforms, coordinates or line membership.','Zero missing names is not independently verified citywide completeness or current-service evidence.']}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--catalogue',type=Path,default=ROOT/'data/moscow-geo.json');parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv);catalog=json.loads(args.catalogue.read_text());manifest=json.loads(args.manifest.read_text());result=compare(catalog,manifest.get('exports'),manifest_root=args.manifest.parent)
    content=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(content)
    else:print(content,end='')
    return 0

if __name__=='__main__':raise SystemExit(main())
