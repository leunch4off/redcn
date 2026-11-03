"""One-command local setup for macOS/Windows. Never starts another server."""
import argparse
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]


def environment(node=None):
    problems = []
    if sys.version_info < (3, 10):
        problems.append('Нужен Python 3.10+: https://www.python.org/downloads/')
    binary = node or shutil.which('node')
    version = ''
    if binary:
        try:
            version = subprocess.check_output([binary, '--version'], text=True, timeout=10).strip()
        except (OSError, subprocess.SubprocessError):
            pass
    match = re.fullmatch(r'v(\d+)\.\d+\.\d+(?:[-+].*)?', version)
    if not match or int(match[1]) < 22:
        problems.append('Нужен Node.js 22+: https://nodejs.org/en/download')
    return {'python':platform.python_version(), 'pythonExecutable':sys.executable,
            'platform':sys.platform, 'node':version, 'nodeExecutable':binary,
            'ready':not problems, 'problems':problems}


def install(root=ROOT, *, check=False, dependencies=True):
    root = Path(root).resolve()
    state = environment()
    if check or not state['ready']:
        return state
    if not (root / 'package.json').is_file() or not (root / 'cli/redcn.py').is_file():
        raise ValueError('Распакуйте полный пакет redcn, сохранив каталоги cli/web/scripts.')
    folder = root / '.venv'
    python = folder / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(folder)
    subprocess.run([str(python), '-c', 'import sys;assert sys.version_info >= (3,10)'], check=True)
    if dependencies:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root/'cli/requirements-media.txt'), '-r', str(root/'cli/requirements-browser.txt')], check=True)
    subprocess.run([state['nodeExecutable'], str(root/'scripts/build.mjs')], cwd=root, check=True)
    subprocess.run([state['nodeExecutable'], str(root/'scripts/validate-artifact.mjs')], cwd=root, check=True)
    subprocess.run([state['nodeExecutable'], str(root/'scripts/validate-pages.mjs')], cwd=root, check=True)
    if (root/'.git').exists():
        subprocess.run([state['nodeExecutable'], str(root/'scripts/install-git-hooks.mjs')], cwd=root, check=True)
    return {**state, 'installed':True, 'launch':[str(python),str(root/'cli/redcn.py'),'--serve','--phone'],
            'message':'Готово. Существующий сервер и база данных не изменялись. Запустите указанную команду для открытия сайта.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Только проверить версии, ничего не устанавливать')
    parser.add_argument('--no-dependencies', action='store_true', help='Пропустить pip при уже установленных зависимостях')
    args = parser.parse_args()
    result = install(check=args.check, dependencies=not args.no_dependencies)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
