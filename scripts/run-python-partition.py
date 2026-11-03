"""Run a deterministic module partition without dropping any discovered tests."""
import argparse
from collections import Counter
import faulthandler
import hashlib
from pathlib import Path
import re
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'cli')]


def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item


def partition(case,parts):
    return int.from_bytes(hashlib.sha256(type(case).__module__.encode()).digest()[:8],'big')%parts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--part',type=int,default=0)
    parser.add_argument('--parts',type=int,default=4)
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    if not 1<=args.parts<=16 or not 0<=args.part<args.parts:parser.error('Invalid partition range')
    cases=list(flatten(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')))
    groups=[[case for case in cases if partition(case,args.parts)==index] for index in range(args.parts)]
    # Preserve occurrences too: imported fixture classes can have repeated IDs.
    if Counter(case.id() for group in groups for case in group)!=Counter(case.id() for case in cases):
        raise RuntimeError('Partition coverage differs from full discovery')
    print('Discovered:',len(cases),'· partitions:',[len(group) for group in groups],flush=True)
    if args.verify_only:return 0
    selected=groups[args.part]
    if not selected:raise RuntimeError('Empty test partition')
    print('Running partition',args.part+1,'of',args.parts,flush=True)
    # Preserve a stack trace if an acceptance fixture stalls in a worker or
    # server shutdown. The trace is diagnostic only: no test is skipped or
    # shortened, and the workflow still owns the total execution deadline.
    faulthandler.dump_traceback_later(120,repeat=True)
    try:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    finally:
        faulthandler.cancel_dump_traceback_later()
    mac_pdf='test_card_assets.CardAssetTests.test_real_mac_chrome_pdf_from_static_unicode_html_and_embedded_photo'
    missing_browser=[]
    for case,reason in result.skipped:
        if case.id()==mac_pdf and sys.platform!='darwin':
            print('Expected platform-only skip:',case.id(),flush=True)
            continue
        if re.search(r'Playwright|(?:Optional|unavailable).{0,30}Chrome|Chrome.{0,30}(?:unavailable|acceptance)',reason,re.I):
            missing_browser.append(reason)
    if missing_browser:print('Installed browser acceptance was skipped:',missing_browser,file=sys.stderr)
    return 0 if result.wasSuccessful() and not missing_browser else 1


if __name__=='__main__':raise SystemExit(main())
