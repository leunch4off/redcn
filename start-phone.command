#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
exec bash ./start-redcn.command --phone "$@"
