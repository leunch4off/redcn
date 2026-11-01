#!/bin/bash
set -euo pipefail
redcn_install_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for redcn_python in /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
  if "$redcn_python" -c 'import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
    exec "$redcn_python" "$redcn_install_root/scripts/install-redcn.py" "$@"
  fi
done
echo 'Установите Python 3.10+ с https://www.python.org/downloads/' >&2
exit 2
