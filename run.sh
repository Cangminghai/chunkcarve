#!/usr/bin/env sh
# ChunkCarve - Linux/macOS launcher (browser mode; desktop window needs pywebview + GTK/Qt)
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -c "import numpy, PIL" 2>/dev/null || {
  echo "[ERROR] missing deps: pip install -r requirements.txt"
  exit 1
}
exec "$PY" -m mcmap.server "$@"
