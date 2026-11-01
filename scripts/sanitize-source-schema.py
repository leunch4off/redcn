#!/usr/bin/env python3
"""Produce a non-executable anonymized DOM fixture from a permitted capture."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from source_schema_fixture import from_file,browser_capture_script
p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',nargs='?');p.add_argument('--output');p.add_argument('--browser-script',action='store_true');a=p.parse_args()
if a.browser_script:print(browser_capture_script());raise SystemExit()
if not a.input or not a.output:p.error('input and --output are required')
source,dest=Path(a.input).resolve(),Path(a.output).resolve()
if source==dest:raise ValueError('Original capture must not be overwritten')
html,metadata=from_file(source);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(html)
dest.with_suffix('.manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(dest),'platform':metadata['platform'],'sanitizedSha256':metadata['sanitizedSha256'],'sections':metadata['observedSections'],'retainedTags':metadata['retainedTags']}))
