"""Une el jugador oficial con su analítica scrapeada y con el próximo rival de su equipo,
respetando la regla de degradación.

Este módulo es el que garantiza R26/T5.6: **un jugador sin match se sirve como analítica
"no disponible", nunca con datos de otro jugador ni con campos vacíos.** Es el único
punto donde ambas fuentes se juntan, para que la regla no dependa de que cada llamador
se acuerde de aplicarla.
"""

from __future__ import annotations

import logging

from fantasy.analytics.presentacion import BloqueAnalitico, ProximoPartido, ProximoRival
from fantasy.matching.emparejador import POSICION_ENTRENADOR, SITIO, Emparejamiento
from fantasy.matching.equipos import equipo_futbolfantasy
from fantasy.scrapers.parsers import PartidoScrapeado, ProbabilidadScrapeada, TendenciaScrapeada

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


MOTIVO_RIVAL_ENTRENADOR = "los entrenadores no llevan próximo rival"
MOTIVO_RIVAL_EQUIPO_SIN_MAPEAR = "equipo sin correspondencia en el sitio de analítica"
MOTIVO_RIVAL_SIN_PARTIDO = "sin datos del próximo partido de su equipo"


def construir_proximo_rival(
    equipo_id_oficial: int | str,
    partidos: dict[str, PartidoScrapeado],
    posicion: str | None = None,
) -> ProximoRival:
    """Devuelve el próximo rival del jugador, o "sin dato" con el motivo.

    `partidos` va indexado por el id de futbolfantasy del EQUIPO. El cruce va por el mapa
    de equipos, que es exacto y versionado: NO pasa por el emparejamiento de jugadores,
    así que un jugador sin emparejar también tiene rival (specs/proximo-rival, D1).
    """
    if posicion == POSICION_ENTRENADOR:
        # Decisión del usuario (2026-10-18), no falta de dato: el de su equipo existe.
        return ProximoRival.sin_dato(MOTIVO_RIVAL_ENTRENADOR)

    equipo = equipo_futbolfantasy(equipo_id_oficial)
    if equipo is None:
        return ProximoRival.sin_dato(MOTIVO_RIVAL_EQUIPO_SIN_MAPEAR)

    partido = partidos.get(equipo)
    if partido is None:
        return ProximoRival.sin_dato(MOTIVO_RIVAL_SIN_PARTIDO)

    return ProximoRival(
        partido=ProximoPartido(
            rival=partido.rival_nombre,
            en_casa=partido.en_casa,
            jornada=partido.jornada,
            origen=SITIO,
            capturado_en=partido.capturado_en,
        )
    )
