#!/usr/bin/env python3
"""Open the owner's existing local editor without printing credentials."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from local_server import open_existing


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',type=Path,default=Path.home()/'.cache'/'redcn')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--card')
    args=parser.parse_args(argv)
    open_existing(args.cache_dir,args.port,args.card)
    print('Локальный редактор открыт для владельца рабочего пространства.')
    return 0


if __name__=='__main__':raise SystemExit(main())
