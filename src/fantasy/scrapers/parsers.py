"""Parsers de futbolfantasy.com: tendencia de valor y probabilidad de jugar.

Estructura verificada contra HTML real el 2026-08-27 (ver design.md §Fuente 2 y
`data/raw/scraping/`). Ambos datos vienen como **atributos `data-*` estructurados**, no
como texto libre — mucho más estable ante cambios de maquetación que parsear texto.

Regla de degradación (R23, no negociable): si el sitio cambió y no hay nada que parsear,
se devuelve vacío/`unavailable` **con motivo**, nunca una excepción que suba hasta la
petición web. Contrasta a propósito con `official/`, que sí lanza fuerte: la API oficial
es la fuente de verdad y un cambio ahí es grave; el scraping es prescindible por diseño.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from fantasy.analytics.presentacion import MediaSemanal, ProbabilidadJugar, TendenciaValor

logger = logging.getLogger("fantasy.scrapers.parsers")

ORIGEN_MERCADO = "futbolfantasy.com"
ORIGEN_PROBABILIDAD = "futbolfantasy.com"


@dataclass(frozen=True)
class TendenciaScrapeada:
    """Tendencia de un jugador tal y como sale de la tabla de mercado, con su
    identificador propio del sitio (no es el id oficial de LaLiga — eso es el paso 5).

    Incluye `equipo_externo` porque el matching **acota por equipo antes de comparar
    nombres**: sin ese dato el emparejamiento pierde su principal salvaguarda contra
    emparejar a dos jugadores homónimos de equipos distintos (ver design.md §Cruce de IDs).
    """

    id_futbolfantasy: str
    nombre: str
    equipo_externo: str
    tendencia: TendenciaValor
    capturado_en: datetime
    # Media de los últimos 7 días. Opcional: alguna fila puede no traerla.
    media_semanal: MediaSemanal | None = None


@dataclass(frozen=True)
class ProbabilidadScrapeada:
    # Id numérico de futbolfantasy: el MISMO que usa la tabla de mercado
    # (`TendenciaScrapeada.id_futbolfantasy`), así que el cruce entre ambas páginas se
    # hace por id exacto, sin comparar nombres. Verificado con datos reales (2026-09-02):
    # confirma como fichero de identidad, no como aproximación.
    id_futbolfantasy: str
    slug: str
    probabilidad: ProbabilidadJugar
    capturado_en: datetime


def _direccion(diferencia_euros: int) -> str:
    if diferencia_euros > 0:
        return "sube"
    if diferencia_euros < 0:
        return "baja"
    return "estable"


def parsear_tendencias_mercado(html: str) -> list[TendenciaScrapeada]:
    """Página `/analytics/laliga-fantasy/mercado`: una fila `<tr class="elemento_jugador">`
    por jugador, con `data-id`, `data-nombre` y `data-diferencia1` (variación de las
    últimas 24h en euros, que es la cadencia de nuestro snapshot diario).

    Degradación: fila individual sin los atributos esperados -> se salta con warning, no
    aborta el resto. Si no hay NINGUNA fila -> lista vacía con warning (el sitio cambió).
    """
    ahora = datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    filas = soup.select("tr.elemento_jugador[data-id]")

    if not filas:
        logger.warning("parsear_tendencias_mercado: 0 filas encontradas; ¿cambió el sitio?")
        return []

    resultados = []
    for fila in filas:
        try:
            id_ff = fila["data-id"]
            nombre = fila["data-nombre"]
            equipo = fila["data-equipo"]
            diferencia = int(fila["data-diferencia1"])
            # `data-diferencia7` es la variación ACUMULADA de la semana, no la media:
            # dividir entre 7 es lo que la hace comparable con la variación diaria.
            semanal = fila.get("data-diferencia7")
            media_semanal = (
                MediaSemanal(media_diaria_euros=round(int(semanal) / 7), acumulado_euros=int(semanal))
                if semanal not in (None, "")
                else None
            )
            tendencia = TendenciaScrapeada(
                id_futbolfantasy=id_ff,
                nombre=nombre,
                equipo_externo=equipo,
                tendencia=TendenciaValor(
                    direccion=_direccion(diferencia), variacion_euros=abs(diferencia)
                ),
                capturado_en=ahora,
                media_semanal=media_semanal,
            )
        except (KeyError, ValueError) as exc:
            logger.warning("fila de mercado con atributos inesperados, se salta: %s", exc)
            continue

        resultados.append(tendencia)

    logger.info("parsear_tendencias_mercado: %d/%d filas parseadas", len(resultados), len(filas))
    return resultados


def parsear_probabilidad_equipo(html: str) -> list[ProbabilidadScrapeada]:
    """Página `/laliga/equipos/{slug}`: el widget del "campo" (alineación probable
    dibujada como campo de fútbol) envuelve a cada jugador en un
    `<div class="jugador_{id} ...">`, con `{id}` el mismo id numérico de futbolfantasy
    que usa la tabla de mercado. Dentro va `<span class="probabilidad-widget">NN%</span>`.

    Se prefiere este widget al de `<a class="camiseta" data-probabilidad="NN%">` que se
    usó antes: aquel solo cubre un subconjunto del equipo (20/26 en una comprobación
    real) y deja fuera a titulares habituales. Verificado con datos reales
    (2026-09-02): este cubre la plantilla entera (26/26) y con id exacto, sin falta de
    comparar nombres — con lo que además desaparece la necesidad de tolerar variantes de
    escritura para este cruce.

    Misma regla de degradación que el mercado.
    """
    ahora = datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    bloques = soup.select('div[class*="jugador_"]')

    if not bloques:
        logger.warning("parsear_probabilidad_equipo: 0 jugadores encontrados; ¿cambió el sitio?")
        return []

    resultados = []
    vistos: set[str] = set()
    for bloque in bloques:
        clase_id = next(
            (c for c in bloque.get("class", []) if re.fullmatch(r"jugador_\d+", c)), None
        )
        if clase_id is None:
            continue
        id_futbolfantasy = clase_id.split("_", 1)[1]
        if id_futbolfantasy in vistos:
            continue  # el mismo jugador puede aparecer en más de un bloque de la página

        span = bloque.select_one("span.probabilidad-widget")
        if span is None:
            continue
        texto = span.get_text(strip=True)
        if not texto:
            continue

        try:
            porcentaje = int(float(texto.rstrip("%")))
        except ValueError as exc:
            logger.warning("probabilidad con formato inesperado '%s', se salta: %s", texto, exc)
            continue

        # El slug es best-effort (para trazabilidad); el cruce real usa el id numérico.
        enlace_real = bloque.find("a", href=lambda h: h and h != "#" and "/jugadores/" in h)
        slug = ""
        if enlace_real is not None:
            resto = enlace_real["href"].split("/jugadores/", 1)[1]
            slug = resto.split("/", 1)[0]

        vistos.add(id_futbolfantasy)
        resultados.append(
            ProbabilidadScrapeada(
                id_futbolfantasy=id_futbolfantasy,
                slug=slug,
                probabilidad=ProbabilidadJugar(porcentaje=porcentaje),
                capturado_en=ahora,
            )
        )

    logger.info(
        "parsear_probabilidad_equipo: %d/%d bloques parseados", len(resultados), len(bloques)
    )
    return resultados
