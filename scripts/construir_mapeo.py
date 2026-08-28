#!/usr/bin/env python3
"""Empareja los jugadores del mercado oficial con los de futbolfantasy y persiste el mapeo.

Imprime un resumen con el **contador de no emparejados** (R27): una subida brusca de ese
número es la señal de que algo se rompió upstream — un cambio de nombres, un equipo nuevo,
o el sitio scrapeado reestructurado.

Uso:
    uv run python scripts/construir_mapeo.py                # solo informa
    uv run python scripts/construir_mapeo.py --guardar      # persiste en la base de datos
    uv run python scripts/construir_mapeo.py --sin-red      # usa data/raw/, no toca la red
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bs4 import BeautifulSoup
from sqlalchemy import select

from fantasy.matching.emparejador import SITIO, CandidatoExterno, emparejar
from fantasy.matching.overrides import cargar_overrides
from fantasy.official.modelos import parsear_mercado
from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.degradacion import URL_MERCADO
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import Jugador, MapeoJugador, OrigenMapeo

RAIZ = Path(__file__).resolve().parent.parent


def _ultimo_raw(patron: str) -> Path:
    ficheros = sorted(glob.glob(str(RAIZ / "data" / "raw" / patron)))
    if not ficheros:
        sys.exit(f"No hay ficheros que casen con {patron}; ejecuta antes recon_oficial.py")
    return Path(ficheros[-1])


def _candidatos_desde_html(html: str) -> list[CandidatoExterno]:
    soup = BeautifulSoup(html, "html.parser")
    candidatos = []
    for fila in soup.select("tr.elemento_jugador[data-id]"):
        equipo = fila.get("data-equipo")
        if not equipo:
            continue
        candidatos.append(
            CandidatoExterno(
                id_externo=fila["data-id"],
                nombre=fila.get("data-nombre", ""),
                equipo_externo=equipo,
            )
        )
    return candidatos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guardar", action="store_true", help="Persiste el mapeo en la base de datos")
    parser.add_argument("--sin-red", action="store_true", help="Usa el HTML ya guardado en data/raw/")
    args = parser.parse_args()

    subastas = parsear_mercado(json.loads(_ultimo_raw("*_market.json").read_text()))
    oficiales = [s.jugador for s in subastas]

    if args.sin_red:
        html = (RAIZ / "data" / "raw" / "scraping" / "mercado_analytics.html").read_text(encoding="utf-8")
    else:
        html = ClienteScraping().get_html(URL_MERCADO)
    candidatos = _candidatos_desde_html(html)

    overrides = cargar_overrides()
    emparejados, sin_emparejar = emparejar(oficiales, candidatos, overrides=overrides)

    por_override = sum(1 for e in emparejados if e.id_oficial in overrides)
    por_heuristica = len(emparejados) - por_override

    print(f"\nJugadores oficiales en mercado: {len(oficiales)} (entrenadores excluidos del matching)")
    print(f"Candidatos scrapeados: {len(candidatos)}\n")
    print(f"{'jugador':<24} {'candidato futbolfantasy':<26} confianza")
    print("-" * 62)
    for e in sorted(emparejados, key=lambda x: x.confianza):
        marca = " (override)" if e.id_oficial in overrides else ""
        print(f"{e.candidato.nombre[:23]:<24} {e.candidato.id_externo:<26} {e.confianza:.2f}{marca}")

    print("\n=== RESUMEN ===")
    print(f"  emparejados por heurística : {por_heuristica}")
    print(f"  emparejados por override   : {por_override}")
    print(f"  SIN EMPAREJAR              : {len(sin_emparejar)}")

    if sin_emparejar:
        print("\n  Sin emparejar (se sirven como 'analítica no disponible'):")
        for s in sin_emparejar:
            print(f"    [{s.id_oficial}] {s.apodo}: {s.motivo}")
        print(f"\n  Para corregir a mano, añade a data/mappings/overrides.json:")
        print(f'    "{sin_emparejar[0].id_oficial}": {{"id_externo": "<id de futbolfantasy>", "nota": "..."}}')

    if args.guardar:
        fabrica = obtener_fabrica_sesiones()
        with fabrica() as sesion:
            for oficial in oficiales:
                if sesion.get(Jugador, oficial.id) is None:
                    sesion.add(Jugador(
                        id=oficial.id, nombre=oficial.nombre, apodo=oficial.apodo,
                        slug=oficial.slug, equipo_id=oficial.equipo_id, posicion=oficial.posicion,
                    ))
            sesion.flush()

            for e in emparejados:
                fila = sesion.scalar(
                    select(MapeoJugador).where(
                        MapeoJugador.player_id == e.id_oficial, MapeoJugador.sitio == SITIO
                    )
                )
                origen = (
                    OrigenMapeo.OVERRIDE_MANUAL if e.id_oficial in overrides else OrigenMapeo.HEURISTICA
                )
                if fila is None:
                    sesion.add(MapeoJugador(
                        player_id=e.id_oficial, sitio=SITIO, id_externo=e.candidato.id_externo,
                        nombre_externo=e.candidato.nombre, origen=origen, confianza=e.confianza,
                    ))
                else:
                    fila.id_externo = e.candidato.id_externo
                    fila.nombre_externo = e.candidato.nombre
                    fila.origen = origen
                    fila.confianza = e.confianza
            sesion.commit()
        print(f"\n  Guardados {len(emparejados)} mapeos en la base de datos.")


if __name__ == "__main__":
    main()
