#!/usr/bin/env bash
# Capturas de la maqueta para la verificación visual de R4.
#
# Chrome headless impone un ancho mínimo de ventana (~500px): pedirle --window-size=375
# devuelve un recorte del render a 500px, no un render a 375px. Para tener un viewport
# real de móvil se embebe la página en un iframe de 375px dentro de una ventana ancha.
set -uo pipefail
cd "$(dirname "$0")/.."

PUERTO="${PUERTO:-8010}"
DESTINO="${DESTINO:-/tmp/capturas-fantasy}"
mkdir -p "$DESTINO"

uv run uvicorn fantasy.api.app:app --port "$PUERTO" >/tmp/fantasy-scout.log 2>&1 &
servidor=$!
trap 'kill $servidor 2>/dev/null' EXIT
for _ in $(seq 1 40); do
  curl -sf -o /dev/null "localhost:${PUERTO}/health" && break
  sleep 0.5
done

for pagina in plantilla mercado; do
  # Móvil real: iframe de 375px de ancho.
  cat > "/tmp/marco-${pagina}.html" <<HTML
<!doctype html><meta charset="utf-8">
<body style="margin:0;background:#888">
<iframe src="http://localhost:${PUERTO}/${pagina}"
        style="width:375px;height:1400px;border:0;display:block"></iframe>
HTML
  google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
    --window-size=375,1400 --screenshot="$DESTINO/${pagina}-375.png" \
    "file:///tmp/marco-${pagina}.html" >/dev/null 2>&1

  # Escritorio: ventana directa, por encima del mínimo de Chrome.
  google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
    --window-size=1280,900 --screenshot="$DESTINO/${pagina}-1280.png" \
    "http://localhost:${PUERTO}/${pagina}" >/dev/null 2>&1
done

ls -la "$DESTINO"
