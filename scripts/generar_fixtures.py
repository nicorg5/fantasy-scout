#!/usr/bin/env python3
"""Genera fixtures de test a partir de respuestas reales de `data/raw/`, anonimizadas.

Las fixtures **se versionan en un repo público**, así que no pueden llevar identificadores
ni nombres reales de la liga privada del usuario. Lo que sí debe conservarse intacto es la
*forma* de los datos: es lo único que los tests comprueban.

Se conservan los nombres de futbolistas porque son públicos; se sustituyen los de managers,
los ids de liga/manager/equipo, y el dinero.

Uso:
    uv run python scripts/generar_fixtures.py
"""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
DIR_FIXTURES = RAIZ / "tests" / "fixtures"

# Valores ficticios estables: los tests pueden afirmar sobre ellos sin filtrar nada real.
LIGA_FALSA = "012345678"
LIGA_FALSA_INT = 2345678
MANAGER_FALSO = 111111
EQUIPO_FALSO = 22222222

CLAVES_A_FALSEAR = {
    "managerId": MANAGER_FALSO,
    "leagueId": LIGA_FALSA_INT,
    "teamMoney": 1_000_000,
}
CLAVES_A_ELIMINAR = {"manager", "sellerTeam", "playerTeam", "directOffer"}


def anonimizar(nodo: Any) -> Any:
    if isinstance(nodo, dict):
        limpio = {}
        for clave, valor in nodo.items():
            if clave in CLAVES_A_ELIMINAR:
                continue
            if clave in CLAVES_A_FALSEAR:
                limpio[clave] = CLAVES_A_FALSEAR[clave]
                continue
            limpio[clave] = anonimizar(valor)
        return limpio
    if isinstance(nodo, list):
        return [anonimizar(x) for x in nodo]
    return nodo


def ultimo(patron: str) -> Path:
    ficheros = sorted(glob.glob(str(RAIZ / "data" / "raw" / patron)))
    if not ficheros:
        raise SystemExit(f"No hay ficheros que casen con {patron}; ejecuta antes recon_oficial.py")
    return Path(ficheros[-1])


def main() -> None:
    DIR_FIXTURES.mkdir(parents=True, exist_ok=True)

    crudo_mercado = json.loads(ultimo("*_market.json").read_text())
    libres = [e for e in crudo_mercado if e.get("discr") == "marketPlayerLeague"][:2]
    de_managers = [e for e in crudo_mercado if e.get("discr") == "marketPlayerTeam"][:1]
    # Se incluye uno de manager a propósito: el test verifica que se filtra por `discr`.
    mercado = anonimizar(libres + de_managers)

    crudo_plantilla = json.loads(ultimo("*_plantilla.json").read_text())
    crudo_plantilla["players"] = crudo_plantilla["players"][:2]
    plantilla = anonimizar(crudo_plantilla)
    plantilla["id"] = EQUIPO_FALSO

    for nombre, datos in (("market", mercado), ("plantilla", plantilla)):
        destino = DIR_FIXTURES / f"{nombre}.json"
        destino.write_text(json.dumps(datos, ensure_ascii=False, indent=1))
        print(f"  {destino.relative_to(RAIZ)} ({destino.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
