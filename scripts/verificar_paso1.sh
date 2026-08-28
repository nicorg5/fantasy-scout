#!/usr/bin/env bash
# Verificación manual del paso 1 (R1-R5). Levanta la app, comprueba y la para.
# Puerto configurable: PUERTO=8010 bash scripts/verificar_paso1.sh
set -uo pipefail
cd "$(dirname "$0")/.."

PUERTO="${PUERTO:-8000}"
BASE="localhost:${PUERTO}"

echo "=== R1: data/raw y data/cache ignorados, data/mappings versionado ==="
touch data/raw/prueba.json data/cache/prueba.html
echo "filas de raw/cache en git status (esperado 0): $(git status --porcelain -uall | grep -cE 'data/(raw|cache)')"
git check-ignore -q data/mappings/
echo "git check-ignore data/mappings/ -> exit $? (esperado 1)"
rm -f data/raw/prueba.json data/cache/prueba.html

echo
echo "=== R4: sin JS propio ni build step ==="
echo "ficheros .js propios: $(find . -name '*.js' -not -path './.venv/*' -not -path './.git/*' | wc -l)"
echo "package.json: $(test -f package.json && echo 'EXISTE (mal)' || echo 'no existe (bien)')"

echo
echo "=== R2/R3/R5: app en marcha en ${BASE} ==="
uv run uvicorn fantasy.api.app:app --port "${PUERTO}" >/tmp/fantasy-scout.log 2>&1 &
servidor=$!
trap 'kill $servidor 2>/dev/null' EXIT
for _ in $(seq 1 40); do
  curl -sf -o /dev/null "${BASE}/health" && break
  sleep 0.5
done

echo "R2 /health        -> $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/health") $(curl -s "${BASE}/health")"
echo "R2 /static        -> $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/static/estilos.css")"
echo "R3 /plantilla     -> $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/plantilla")"
echo "R3 /mercado       -> $(curl -s -o /dev/null -w '%{http_code}' "${BASE}/mercado")"

curl -s "${BASE}/plantilla" >/tmp/fantasy-plantilla.html
curl -s "${BASE}/mercado"   >/tmp/fantasy-mercado.html

for metrica in 'Valor de mercado' 'Tendencia de valor' 'Probabilidad de jugar'; do
  echo "R3 métrica '$metrica' -> $(grep -c "$metrica" /tmp/fantasy-plantilla.html) apariciones"
done
echo "R3 jugador mock   -> $(grep -c 'Iker Valdeón' /tmp/fantasy-plantilla.html) apariciones"

for pagina in plantilla mercado; do
  echo "R5 badge en $pagina -> $(grep -c 'analítica no disponible' "/tmp/fantasy-$pagina.html")"
  echo "R5 celdas con 0 o guion mudo en $pagina -> $(grep -cE '<td[^>]*>[[:space:]]*[-0][[:space:]]*</td>' "/tmp/fantasy-$pagina.html")"
done
echo "R5 origen etiquetado -> $(grep -c 'fuente:' /tmp/fantasy-plantilla.html) etiquetas"

echo
echo "=== errores en el log del servidor ==="
grep -iE 'error|traceback' /tmp/fantasy-scout.log || echo "ninguno"
