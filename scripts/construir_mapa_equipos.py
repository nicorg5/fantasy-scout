#!/usr/bin/env python3
"""Construye el puente entre los IDs de equipo de la API oficial y los de futbolfantasy.

Los identificadores NO coinciden (Málaga es 12 en oficial y 11 en futbolfantasy) y ambos
son enteros pequeños, así que cruzarlos directamente emparejaría equipos equivocados **sin
dar error**. Ver design.md §Cruce de IDs.

Método: votación. Se toman los jugadores que casan por nombre exacto y de forma inequívoca
(un único candidato a cada lado) y se cuenta a qué equipo apunta cada uno en ambos lados.
Si la votación no es unánime para algún equipo, se avisa: es señal de que algo cambió.

El resultado se versiona en `data/mappings/equipos.json` y NO se recalcula en cada
ejecución: es estable durante la temporada.

Uso:
    uv run python scripts/construir_mapa_equipos.py            # muestra el resultado
    uv run python scripts/construir_mapa_equipos.py --escribir # lo guarda
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import re

from bs4 import BeautifulSoup

from fantasy.matching.normalizacion import normalizar

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "data" / "mappings" / "equipos.json"


def _ultimo(patron: str) -> Path:
    ficheros = sorted(glob.glob(str(RAIZ / "data" / "raw" / patron)))
    if not ficheros:
        sys.exit(f"No hay ficheros que casen con {patron}; ejecuta antes recon_oficial.py")
    return Path(ficheros[-1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--escribir", action="store_true", help="Guarda data/mappings/equipos.json")
    args = parser.parse_args()

    oficiales = json.loads(_ultimo("*_players.json").read_text())
    html = (RAIZ / "data" / "raw" / "scraping" / "mercado_analytics.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    por_nombre_oficial: dict[str, list] = defaultdict(list)
    for p in oficiales:
        por_nombre_oficial[normalizar(p["nickname"])].append(p)

    por_nombre_ff: dict[str, list] = defaultdict(list)
    for tr in soup.select("tr.elemento_jugador[data-id]"):
        por_nombre_ff[normalizar(tr.get("data-nombre", ""))].append(tr.get("data-equipo"))

    votos: dict[str, Counter] = defaultdict(Counter)
    for nombre, oficiales_con_ese_nombre in por_nombre_oficial.items():
        candidatos = por_nombre_ff.get(nombre)
        # Solo pares inequívocos: un único jugador con ese nombre a CADA lado.
        if candidatos and len(oficiales_con_ese_nombre) == 1 and len(candidatos) == 1:
            votos[str(oficiales_con_ese_nombre[0]["teamId"])][candidatos[0]] += 1

    # Los slugs hacen falta aparte del id numérico: la tabla de mercado usa el id
    # (data-equipo="11") pero la URL de la página de equipo usa el slug
    # (/laliga/equipos/malaga). Se extraen del mismo HTML, emparejando el escudo —que
    # lleva el id— con el enlace contiguo.
    slug_por_id: dict[str, str] = {}
    for id_equipo, slug in re.findall(
        r'cabecera/\w+/(\d+)\.\w+[^>]*>.{0,400}?/laliga/equipos/([a-z0-9-]+)"', html, re.S
    ):
        slug_por_id.setdefault(id_equipo, slug)

    # El nombre legible sale del alt del escudo. Sin él la UI mostraría el id numérico,
    # que no significa nada para quien mira la pantalla.
    nombre_por_id: dict[str, str] = {}
    for id_equipo, nombre in re.findall(
        r'cabecera/\w+/(\d+)\.\w+[^>]*alt="Escudo ([^"]+)"', html
    ):
        nombre_por_id.setdefault(id_equipo, nombre.strip())

    mapa: dict[str, dict[str, str]] = {}
    conflictos = []
    sin_slug = []
    for team_id, cuenta in votos.items():
        ganador, _ = cuenta.most_common(1)[0]
        slug = slug_por_id.get(ganador)
        if slug is None:
            sin_slug.append((team_id, ganador))
        mapa[team_id] = {
            "id": ganador,
            "slug": slug or "",
            "nombre": nombre_por_id.get(ganador, ""),
        }
        if len(cuenta) > 1:
            conflictos.append((team_id, dict(cuenta)))

    print(f"{len(mapa)} equipos mapeados a partir de {sum(sum(c.values()) for c in votos.values())} votos\n")
    for team_id in sorted(mapa, key=int):
        cuenta = votos[team_id]
        entrada = mapa[team_id]
        print(
            f"  oficial {team_id:>3} -> ff {entrada['id']:>3} "
            f"{entrada['nombre'] or '(sin nombre)':<18} "
            f"({entrada['slug'] or 'SIN SLUG':<16}) {sum(cuenta.values())} votos"
        )

    if sin_slug:
        print("\n⚠ equipos sin slug (no se podrá leer su probabilidad de jugar):")
        for team_id, id_ff in sin_slug:
            print(f"    oficial {team_id} -> futbolfantasy {id_ff}")

    if conflictos:
        print("\n⚠ VOTACIÓN NO UNÁNIME — revisar antes de usar:")
        for team_id, cuenta in conflictos:
            print(f"    oficial {team_id}: {cuenta}")
    else:
        print("\nVotación unánime en todos los equipos.")

    if args.escribir:
        DESTINO.parent.mkdir(parents=True, exist_ok=True)
        DESTINO.write_text(json.dumps(mapa, indent=1, sort_keys=True) + "\n")
        print(f"\nEscrito {DESTINO.relative_to(RAIZ)}")
    else:
        print("\n(pasa --escribir para guardarlo)")


if __name__ == "__main__":
    main()
