#!/usr/bin/env bash
# Comprobaciones antes de mergear a main y desplegar a Render.
#
# No automatiza el despliegue a proposito: solo verifica y te dice si algo falta. Mergear
# sigue siendo una decision consciente.
#
# Comprueba las tres trampas concretas de este proyecto:
#   1. Que los tests pasan (contra el Postgres LOCAL, nunca contra Neon).
#   2. Que no quedan migraciones sin aplicar a Neon: si el codigo llega a Render antes
#      que la tabla, la web casca en produccion.
#   3. Que no hay cambios sin commitear que se quedarian fuera del despliegue.
#
# Uso:
#     bash scripts/pre_deploy.sh

set -uo pipefail
cd "$(dirname "$0")/.."

ROJO=$'\033[31m'; VERDE=$'\033[32m'; AMARILLO=$'\033[33m'; FIN=$'\033[0m'
fallos=0
avisos=0

ok()     { echo "  ${VERDE}[ok]${FIN}   $1"; }
error()  { echo "  ${ROJO}[FALLO]${FIN} $1"; fallos=$((fallos+1)); }
aviso()  { echo "  ${AMARILLO}[aviso]${FIN} $1"; avisos=$((avisos+1)); }

echo
echo "=== 1. Rama y estado del repositorio ==="

rama=$(git branch --show-current)
if [ "$rama" = "main" ]; then
  aviso "estas en main. Se trabaja en feature/v0.1.0 (ver design.md)"
else
  ok "rama: $rama"
fi

if [ -n "$(git status --porcelain)" ]; then
  error "hay cambios sin commitear: no llegarian al despliegue"
  git status --short | sed 's/^/         /'
else
  ok "sin cambios pendientes"
fi

echo
echo "=== 2. Base de datos local ==="

if docker compose ps --status running 2>/dev/null | grep -q fantasy_scout_postgres; then
  ok "Postgres local levantado"
else
  aviso "Postgres local parado (se para solo a menudo). Levantando..."
  docker compose up -d >/dev/null 2>&1 && sleep 4
fi

echo
echo "=== 3. Tests (contra Postgres local) ==="

if salida=$(uv run pytest -q 2>&1); then
  ok "$(echo "$salida" | tail -1)"
else
  error "tests en rojo"
  echo "$salida" | tail -15 | sed 's/^/         /'
fi

echo
echo "=== 4. Migraciones pendientes en Neon ==="

if [ ! -f .env.produccion ]; then
  aviso ".env.produccion no existe: no se puede comprobar el estado de Neon"
else
  local_head=$(uv run alembic heads 2>/dev/null | head -1 | awk '{print $1}')
  neon_head=$(uv run --env-file .env.produccion alembic current 2>/dev/null | tail -1 | awk '{print $1}')

  if [ -z "$neon_head" ]; then
    error "no se pudo consultar Neon (revisa .env.produccion)"
  elif [ "$local_head" = "$neon_head" ]; then
    ok "Neon al dia ($neon_head)"
  else
    error "Neon esta en '$neon_head' y el codigo espera '$local_head'"
    echo "         Aplicalo ANTES de mergear, o la web cascara en produccion:"
    echo "           uv run --env-file .env.produccion alembic upgrade head"
  fi
fi

echo
echo "=== 5. Secretos fuera del repo ==="

if git ls-files --error-unmatch .env .env.produccion >/dev/null 2>&1; then
  error "hay ficheros .env versionados"
else
  ok ".env y .env.produccion fuera del control de versiones"
fi

echo
if [ "$fallos" -gt 0 ]; then
  echo "${ROJO}$fallos comprobacion(es) fallida(s). No se recomienda desplegar.${FIN}"
  exit 1
fi

if [ "$avisos" -gt 0 ]; then
  echo "${AMARILLO}Todo correcto, con $avisos aviso(s).${FIN}"
else
  echo "${VERDE}Todo correcto.${FIN}"
fi

cat <<'PASOS'

Siguiente paso recomendado: ensayo contra la base de datos real.

  uv run --env-file .env.produccion uvicorn fantasy.api.app:app --port 8010

Es la app local hablando con Neon: detecta casi todo lo que fallaria en Render.
Si va bien:

  git checkout main && git merge feature/v0.1.0 && git push
  git checkout feature/v0.1.0
PASOS
