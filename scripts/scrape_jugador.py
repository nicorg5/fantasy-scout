#!/usr/bin/env python3
"""Scrapea tendencia de valor y probabilidad de jugar de un futbolista, por nombre.

El matching real por ID (paso 5) todavía no existe; este script busca por coincidencia
de nombre/slug, útil para probar el scraper de forma aislada.

Uso:
    uv run python scripts/scrape_jugador.py "vinicius junior"
    uv run python scripts/scrape_jugador.py "rafa garrido" --equipo malaga
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.parsers import parsear_probabilidad_equipo, parsear_tendencias_mercado

URL_MERCADO = "https://www.futbolfantasy.com/analytics/laliga-fantasy/mercado"


def _url_equipo(slug: str) -> str:
    return f"https://www.futbolfantasy.com/laliga/equipos/{slug}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nombre", help="Nombre (o parte) del jugador, tal como aparece en la web")
    parser.add_argument(
        "--equipo",
        default=None,
        help="Slug del equipo en futbolfantasy.com (ej. 'malaga'), para buscar probabilidad",
    )
    args = parser.parse_args()

    cliente = ClienteScraping()
    objetivo = args.nombre.strip().lower()

    print(f"Buscando tendencia de valor para {args.nombre!r}...")
    html_mercado = cliente.get_html(URL_MERCADO)
    tendencias = parsear_tendencias_mercado(html_mercado)
    coincidencias = [t for t in tendencias if objetivo in t.nombre.lower()]

    if not coincidencias:
        print(f"  [FUENTE: futbolfantasy.com] no encontrado en mercado (¿nombre exacto?)")
    for t in coincidencias:
        print(
            f"  [FUENTE: futbolfantasy.com | {t.capturado_en:%Y-%m-%d %H:%M}] "
            f"{t.nombre}: tendencia {t.tendencia.simbolo} {t.tendencia.variacion_euros:,} €"
        )

    if args.equipo:
        print(f"\nBuscando probabilidad de jugar en el equipo {args.equipo!r}...")
        html_equipo = cliente.get_html(_url_equipo(args.equipo))
        probabilidades = parsear_probabilidad_equipo(html_equipo)
        coincidencias_slug = [p for p in probabilidades if objetivo.replace(" ", "-") in p.slug]

        if not coincidencias_slug:
            print(f"  [FUENTE: futbolfantasy.com] no encontrado en la plantilla de {args.equipo}")
        for p in coincidencias_slug:
            print(
                f"  [FUENTE: futbolfantasy.com | {p.capturado_en:%Y-%m-%d %H:%M}] "
                f"{p.slug}: probabilidad de jugar {p.probabilidad.porcentaje}%"
            )
    else:
        print("\n(pasa --equipo <slug> para buscar también la probabilidad de jugar)")


if __name__ == "__main__":
    main()
