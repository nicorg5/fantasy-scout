"""Une el jugador oficial con su analítica scrapeada, respetando la regla de degradación.

Este módulo es el que garantiza R26/T5.6: **un jugador sin match se sirve como analítica
"no disponible", nunca con datos de otro jugador ni con campos vacíos.** Es el único
punto donde ambas fuentes se juntan, para que la regla no dependa de que cada llamador
se acuerde de aplicarla.
"""

from __future__ import annotations

import logging

from fantasy.analytics.presentacion import BloqueAnalitico
from fantasy.matching.emparejador import POSICION_ENTRENADOR, SITIO, Emparejamiento
from fantasy.scrapers.parsers import ProbabilidadScrapeada, TendenciaScrapeada

logger = logging.getLogger("fantasy.matching.analitica")


def construir_bloque_analitico(
    id_oficial: str,
    emparejamientos: dict[str, Emparejamiento],
    tendencias: dict[str, TendenciaScrapeada],
    probabilidades: dict[str, ProbabilidadScrapeada] | None = None,
    posicion: str | None = None,
) -> BloqueAnalitico:
    """Devuelve el bloque analítico de un jugador, o `no_disponible` con el motivo.

    `emparejamientos` va indexado por id oficial; `tendencias` y `probabilidades`, por el
    id/slug del sitio scrapeado. La indirección es deliberada: es justo el punto donde un
    mapeo incorrecto haría daño, así que queda concentrado y auditable aquí.
    """
    if posicion == POSICION_ENTRENADOR:
        # No es un fallo: los entrenadores quedan fuera del MVP por decisión del usuario.
        return BloqueAnalitico.no_disponible(
            "los entrenadores no llevan analítica en esta versión", origen=SITIO
        )

    emparejamiento = emparejamientos.get(id_oficial)
    if emparejamiento is None:
        return BloqueAnalitico.no_disponible(
            "jugador sin emparejar con el sitio de analítica", origen=SITIO
        )

    tendencia = tendencias.get(emparejamiento.candidato.id_externo)
    if tendencia is None:
        return BloqueAnalitico.no_disponible(
            "sin datos de tendencia para este jugador hoy", origen=SITIO
        )

    # La probabilidad puede faltar legítimamente: futbolfantasy solo la publica para los
    # jugadores del once probable. En ese caso se sirve la tendencia igualmente, con la
    # probabilidad marcada como ausente y su motivo (decisión del usuario, 2026-08-27).
    probabilidad = (probabilidades or {}).get(emparejamiento.candidato.id_externo)

    return BloqueAnalitico.desde_scraping(
        tendencia_valor=tendencia.tendencia,
        media_semanal=tendencia.media_semanal,
        probabilidad_jugar=probabilidad.probabilidad if probabilidad else None,
        origen=SITIO,
        capturado_en=tendencia.capturado_en,
        motivo_probabilidad=(
            None if probabilidad else "no aparece en el once probable de su equipo"
        ),
    )
