#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v node >/dev/null; then
  echo "Установите Node.js 22+ с https://nodejs.org и повторите запуск."
  read -r -p "Enter — закрыть"; exit 1
fi
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
  echo "Установите Python 3.10+ с https://python.org и повторите запуск."
  read -r -p "Enter — закрыть"; exit 1
fi
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import playwright.sync_api' 2>/dev/null; then
  echo "Первый запуск: устанавливаем библиотеку для управления Google Chrome…"
  .venv/bin/python -m pip install -r cli/requirements-browser.txt
fi
exec .venv/bin/python cli/redcn.py --serve "$@"
