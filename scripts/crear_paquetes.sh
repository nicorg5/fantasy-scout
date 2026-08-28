#!/usr/bin/env bash
# Asegura que todos los subpaquetes de src/fantasy/ tienen su __init__.py.
# Sin él, git no versiona el directorio vacío y el árbol de T1.1 se pierde al clonar.
set -euo pipefail
cd "$(dirname "$0")/.."

for paquete in auth official scrapers matching storage analytics api; do
  mkdir -p "src/fantasy/${paquete}"
  if [ ! -f "src/fantasy/${paquete}/__init__.py" ]; then
    : > "src/fantasy/${paquete}/__init__.py"
    echo "creado src/fantasy/${paquete}/__init__.py"
  fi
done

ls src/fantasy/*/__init__.py
