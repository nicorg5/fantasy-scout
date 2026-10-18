"""Envoltorio de degradación (T4.6/R23): fetch + parseo de una página de mercado o de
equipo, sin que un fallo —de red o de parseo— pueda llegar a tumbar una petición web.

Separado de `parsers.py` a propósito: los parsers son puros (HTML -> datos) y ya
degradan solos ante HTML inesperado; este módulo añade la capa de red, que es donde
puede fallar de más formas (timeout, DNS, 5xx, sitio caído).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.parsers import (
    PartidoScrapeado,
    ProbabilidadScrapeada,
    TendenciaScrapeada,
    parsear_probabilidad_equipo,
    parsear_proximo_partido,
    parsear_tendencias_mercado,
)

logger = logging.getLogger("fantasy.scrapers.degradacion")

URL_MERCADO = "https://www.futbolfantasy.com/analytics/laliga-fantasy/mercado"


def obtener_tendencias_mercado(
    cliente: ClienteScraping, *, ignorar_cache: bool = False
) -> list[TendenciaScrapeada]:
    """Lista vacía en cualquier fallo: red caída, HTTP no-200, o HTML sin selectores.
    Nunca lanza. El llamador no necesita distinguir el motivo, solo saber que no hay
    datos hoy — se registra el detalle real en el log.
    """
    html = cliente.get_html_seguro(URL_MERCADO, ignorar_cache=ignorar_cache)
    if html is None:
        return []
    return parsear_tendencias_mercado(html)


@dataclass(frozen=True)
class PaginaEquipo:
    """Lo que se saca de la página de un equipo. Cada dato degrada por su cuenta: si
    falla el partido, la probabilidad sigue llegando, y al revés (P8)."""

    probabilidades: list[ProbabilidadScrapeada] = field(default_factory=list)
    partido: PartidoScrapeado | None = None


def obtener_pagina_equipo(
    cliente: ClienteScraping, slug_equipo: str, nombre_equipo: str, *, ignorar_cache: bool = False
) -> PaginaEquipo:
    """Descarga la página del equipo UNA vez y extrae de ella todo lo que usamos.

    Una función por dato con su propia descarga saldría de la caché en el cron, pero
    con "Actualizar datos" (`ignorar_cache=True`) haría dos peticiones por equipo, y
    las reglas del scraping piden las mínimas (specs/proximo-rival/design.md §D2).

    `nombre_equipo` es el `nombre` del mapa de equipos: el parser del partido lo
    necesita para saber si el equipo juega en casa.
    """
    html = cliente.get_html_seguro(
        f"https://www.futbolfantasy.com/laliga/equipos/{slug_equipo}", ignorar_cache=ignorar_cache
    )
    if html is None:
        return PaginaEquipo()
    return PaginaEquipo(
        probabilidades=parsear_probabilidad_equipo(html),
        partido=parsear_proximo_partido(html, nombre_equipo),
    )
