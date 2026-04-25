#!/usr/bin/env bash
# Запускает Chainlit поверх HTTPS — нужно, чтобы голосовой ввод работал
# с других машин в сети (браузеры блокируют getUserMedia на http://<lan-ip>).
#
# Сертификат самоподписанный, лежит в certs/tls/ (в .gitignore). Браузер
# покажет предупреждение «Not secure» — Advanced → Proceed.
#
# Параметры можно переопределить переменными окружения:
#   HOST=0.0.0.0 PORT=8000 ./run-tls.sh
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
CERT_DIR="$(dirname "$0")/certs/tls"
CERT="$CERT_DIR/cert.pem"
KEY="$CERT_DIR/key.pem"

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  echo "→ Генерирую self-signed TLS-сертификат в $CERT_DIR/"
  mkdir -p "$CERT_DIR"
  # Поправь IP/DNS под свою сеть, если запускаешься не с этих хостов.
  openssl req -x509 -newkey rsa:2048 \
    -keyout "$KEY" -out "$CERT" \
    -days 365 -nodes \
    -subj "/CN=sokrat-local" \
    -addext "subjectAltName=DNS:localhost,DNS:sokrat-local,IP:127.0.0.1,IP:192.168.10.248"
  echo "→ Готово."
fi

VENV_BIN="$(dirname "$0")/.venv/bin"
CHAINLIT="${VENV_BIN}/chainlit"
[[ -x "$CHAINLIT" ]] || CHAINLIT="chainlit"

exec "$CHAINLIT" run app.py \
  --host "$HOST" --port "$PORT" \
  --ssl-key "$KEY" --ssl-cert "$CERT"
