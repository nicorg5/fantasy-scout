#!/usr/bin/env python3
"""Reconocimiento de la API oficial de LaLiga Fantasy (paso 3, T3.1).

Ejecuta los endpoints conocidos con el token del usuario y guarda las respuestas
**crudas** en `data/raw/`, con timestamp y nombre de endpoint. Esas respuestas son la
única forma barata de diagnosticar qué cambió cuando la API se mueva sin avisar.

El token se lee de `data/raw/token.txt` (gitignored) porque este script es de
reconocimiento manual, anterior a que exista un usuario en la base de datos. El cliente
de producción lo obtiene descifrado del store (ver R17), nunca de un fichero.

Uso:
    uv run python scripts/recon_oficial.py
    uv run python scripts/recon_oficial.py --league-id 012345678
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
DIR_RAW = RAIZ / "data" / "raw"
FICHERO_TOKEN = DIR_RAW / "token.txt"

HOST = "https://fantasy-api.llt-services.com"
COMPETICION = 1


def leer_token() -> str:
    if not FICHERO_TOKEN.exists():
        sys.exit(f"No existe {FICHERO_TOKEN}. Pega ahí el bearer token de LaLiga.")
    crudo = FICHERO_TOKEN.read_text().strip()
    # El usuario puede haber copiado la cabecera entera desde DevTools.
    if crudo.lower().startswith("bearer "):
        crudo = crudo[7:].strip()
    if not crudo:
        sys.exit(f"{FICHERO_TOKEN} está vacío.")
    return crudo


def cabeceras(token: str) -> dict[str, str]:
    """Cabeceras obligatorias. `x-app: 2` no es adivinable: ver design.md §Fuente 1."""
    return {
        "Authorization": f"Bearer {token}",
        "x-app": "2",
        "x-lang": "es",
        "content-type": "application/json",
    }


def guardar(nombre: str, contenido: bytes, sello: str) -> Path:
    DIR_RAW.mkdir(parents=True, exist_ok=True)
    destino = DIR_RAW / f"{sello}_{nombre}.json"
    destino.write_bytes(contenido)
    return destino


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--league-id",
        default=None,
        help="Si se omite, se descubre desde /leagues. Es una CADENA (puede llevar ceros a la izquierda).",
    )
    args = parser.parse_args()

    token = leer_token()
    sello = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    base_v1 = f"{HOST}/api/v1/competition/{COMPETICION}"
    peticiones: list[tuple[str, str]] = [
        ("user_me", f"{HOST}/api/v4/user/me"),
        ("leagues", f"{base_v1}/leagues"),
        ("week_current", f"{base_v1}/week/current"),
    ]

    with httpx.Client(headers=cabeceras(token), timeout=20.0) as cliente:
        league_id = args.league_id
        team_id: int | None = None
        resultados: list[tuple[str, int, Path | None]] = []

        for nombre, url in peticiones:
            r = cliente.get(url, params={"x-lang": "es"})
            destino = guardar(nombre, r.content, sello) if r.status_code == 200 else None
            resultados.append((nombre, r.status_code, destino))

            # Descubrir liga y equipo del usuario si no se pasaron por argumento.
            if nombre == "leagues" and r.status_code == 200:
                datos = r.json()
                ligas = datos if isinstance(datos, list) else datos.get("data", [])
                if ligas:
                    if league_id is None:
                        # Cadena a propósito: puede llevar ceros a la izquierda.
                        league_id = str(ligas[0].get("id"))
                    team_id = (ligas[0].get("team") or {}).get("id")

        if league_id:
            dependientes = [
                ("market", f"{base_v1}/league/{league_id}/market"),
                ("players", f"{base_v1}/players"),
            ]
            if team_id is not None:
                dependientes += [
                    ("plantilla", f"{base_v1}/leagues/{league_id}/teams/{team_id}"),
                    ("lineup", f"{base_v1}/teams/{team_id}/lineup"),
                ]
            for nombre, url in dependientes:
                r = cliente.get(url, params={"x-lang": "es"})
                destino = guardar(nombre, r.content, sello) if r.status_code == 200 else None
                resultados.append((nombre, r.status_code, destino))
        else:
            print("Aviso: no se pudo determinar el leagueId; se omiten los endpoints que lo necesitan.")

    print(f"\nReconocimiento {sello} (liga: {league_id or 'desconocida'})\n")
    for nombre, codigo, destino in resultados:
        marca = "ok " if codigo == 200 else "FALLO"
        detalle = f"{destino.name} ({destino.stat().st_size} bytes)" if destino else "-"
        print(f"  [{marca}] {codigo}  {nombre:14} {detalle}")


if __name__ == "__main__":
    main()
