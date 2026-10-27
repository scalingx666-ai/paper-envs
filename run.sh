#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3.13}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3.13 not found; set PYTHON=/path/to/python3.11+" >&2
  exit 1
fi
exec "$PY" pipeline.py "$@"
