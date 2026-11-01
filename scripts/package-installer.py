"""Package only tracked program inputs; runtime data and credentials never enter."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'cli','web','scripts','docs','data','worker','.githooks','.openai'}
ROOT_FILES = {'package.json','README.md','ROADMAP.md','plan.json','CHANGELOG.md','install-redcn.command','install-redcn.cmd'}


def package(root, target):
    root, target = Path(root).resolve(), Path(target).resolve()
    if target.exists():
        raise ValueError('Пакет уже существует: выберите новое имя')
    names = subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
    manifest = []
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in names:
            if not name:
                continue
            parts = Path(name).parts
            if parts[0] not in ALLOWED and name not in ROOT_FILES:
                continue
            if any(part in ('.git','.venv','__pycache__','node_modules') for part in parts) or re.search(r'(?:^|/)(?:\.env|cookies?|phone-access|auth)[./-]|\.sqlite3',name,re.I):
                raise ValueError('В отслеживаемых файлах обнаружены приватные runtime-данные')
            source = root/name
            if source.is_symlink():
                raise ValueError('Символьные ссылки не упаковываются')
            content = source.read_bytes()
            info = zipfile.ZipInfo('redcn/'+name)
            info.create_system = 3
            info.external_attr = (source.stat().st_mode & 0xffff) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info,content)
            manifest.append({'path':name,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
        archive.writestr('redcn/PACKAGE-MANIFEST.json',json.dumps({'version':1,'files':manifest},ensure_ascii=False,indent=2))
    return {'path':str(target),'files':len(manifest),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    print(json.dumps(package(ROOT,args.output),ensure_ascii=False))
