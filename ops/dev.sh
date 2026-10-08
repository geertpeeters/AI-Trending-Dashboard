#!/bin/sh
# Lokale dev-run: crawl (AI optioneel) + server.
set -e
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 ontbreekt (apk add python3 py3-yaml py3-jinja2)" >&2
  exit 1
fi

python3 -m compileall -q pipeline server
python3 pipeline/crawl.py "$@"
exec python3 server/server.py
