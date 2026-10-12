#!/usr/bin/env bash
# Запуск веб-интерфейса Сократа (лендинг, интервью, отчёт, личный кабинет).
#   HOST=0.0.0.0 PORT=8000 ./run-web.sh
set -euo pipefail
cd "$(dirname "$0")"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
BIN=".venv/bin"
[[ -x "$BIN/uvicorn" ]] || BIN=""
exec ${BIN:+$BIN/}uvicorn web.server:app --host "$HOST" --port "$PORT"
