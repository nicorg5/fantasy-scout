"""Motor de matching entre jugadores oficiales y jugadores de futbolfantasy.

Estrategia decidida con datos reales (ver design.md §Cruce de IDs):
acotar por equipo primero, comparar nombres después. Acotar es lo que hace fiable el
resto: sin ello el acierto medido fue del 27%, y aparecían candidatos de otros equipos
con apellidos parecidos.

Regla dura: ante la duda, **sin match**. Un jugador mal emparejado es peor que un jugador
sin analítica, porque contamina un dato que el usuario mira a diario sin poder detectarlo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher

from fantasy.matching.equipos import equipo_futbolfantasy
from fantasy.matching.normalizacion import normalizar, variantes_oficiales

logger = logging.getLogger("fantasy.matching.emparejador")

SITIO = "futbolfantasy.com"

# Umbral de auto-aceptación, elegido por el usuario (2026-08-27). Deliberadamente
# permisivo: queda holgado respecto al peor caso real entre jugadores de campo (0,91).
UMBRAL_POR_DEFECTO = 0.60

# Si dos candidatos del mismo equipo están así de cerca, no hay forma de decidir cuál es:
# se trata como sin match en vez de elegir el mejor a ciegas.
MARGEN_AMBIGUEDAD = 0.05

# Los entrenadores quedan fuera del MVP (decisión del usuario): además de ser los peores
# casos de matching, futbolfantasy los identifica con IDs de otra forma ('e119').
POSICION_ENTRENADOR = "Entrenador"


@dataclass(frozen=True)
class CandidatoExterno:
    """Un jugador del lado scrapeado, tal como sale de la tabla de futbolfantasy."""

    id_externo: str
    nombre: str
    equipo_externo: str


@dataclass(frozen=True)
class Emparejamiento:
    id_oficial: str
    candidato: CandidatoExterno
    confianza: float


@dataclass(frozen=True)
class SinEmparejar:
    id_oficial: str
    apodo: str
    motivo: str


def _mejor_candidato(
    variantes: set[str], candidatos: list[CandidatoExterno]
) -> tuple[CandidatoExterno | None, float, float]:
    """Devuelve (mejor, similitud_mejor, similitud_segundo).

    La similitud del segundo es lo que permite detectar ambigüedad más arriba.
    """
    puntuados: list[tuple[float, CandidatoExterno]] = []
    for candidato in candidatos:
        normalizado = normalizar(candidato.nombre)
        if normalizado in variantes:
            similitud = 1.0
        else:
            similitud = max(
                SequenceMatcher(None, variante, normalizado).ratio() for variante in variantes
            )
        puntuados.append((similitud, candidato))

    if not puntuados:
        return None, 0.0, 0.0

    puntuados.sort(key=lambda par: par[0], reverse=True)
    mejor_similitud, mejor = puntuados[0]
    segunda = puntuados[1][0] if len(puntuados) > 1 else 0.0
    return mejor, mejor_similitud, segunda


def emparejar(
    jugadores_oficiales: list,
    candidatos: list[CandidatoExterno],
    *,
    umbral: float = UMBRAL_POR_DEFECTO,
    overrides: dict[str, str] | None = None,
) -> tuple[list[Emparejamiento], list[SinEmparejar]]:
    """Empareja jugadores oficiales con candidatos de futbolfantasy.

    `jugadores_oficiales` son `JugadorOficial` de `fantasy.official.modelos`.
    `overrides` mapea id oficial -> id externo y **tiene precedencia absoluta** sobre la
    heurística: es la válvula de escape para cuando esta se equivoca.
    """
    overrides = overrides or {}
    por_equipo: dict[str, list[CandidatoExterno]] = {}
    for candidato in candidatos:
        por_equipo.setdefault(candidato.equipo_externo, []).append(candidato)
    por_id_externo = {c.id_externo: c for c in candidatos}

    emparejados: list[Emparejamiento] = []
    sin_emparejar: list[SinEmparejar] = []

    for oficial in jugadores_oficiales:
        if oficial.posicion == POSICION_ENTRENADOR:
            continue  # fuera del MVP por decisión del usuario

        # 1. Override manual: gana siempre, sin mirar la heurística.
        if oficial.id in overrides:
            id_externo = overrides[oficial.id]
            candidato = por_id_externo.get(id_externo)
            if candidato is None:
                sin_emparejar.append(
                    SinEmparejar(oficial.id, oficial.apodo, f"override apunta a {id_externo!r}, que no está en los datos de hoy")
                )
                continue
            emparejados.append(Emparejamiento(oficial.id, candidato, 1.0))
            continue

        # 2. Acotar por equipo. Sin equipo mapeado no se intenta: buscar entre los 649
        #    candidatos de todos los equipos es justo lo que se midió que no funciona.
        equipo_externo = equipo_futbolfantasy(oficial.equipo_id)
        if equipo_externo is None:
            sin_emparejar.append(
                SinEmparejar(oficial.id, oficial.apodo, f"equipo oficial {oficial.equipo_id} sin mapear")
            )
            continue

        del_equipo = por_equipo.get(equipo_externo, [])
        if not del_equipo:
            sin_emparejar.append(
                SinEmparejar(oficial.id, oficial.apodo, f"sin candidatos en el equipo {equipo_externo}")
            )
            continue

        # 3. Comparar nombres dentro del equipo.
        variantes = variantes_oficiales(oficial.nombre, oficial.apodo, oficial.slug)
        mejor, similitud, segunda = _mejor_candidato(variantes, del_equipo)

        if mejor is None or similitud < umbral:
            sin_emparejar.append(
                SinEmparejar(oficial.id, oficial.apodo, f"mejor similitud {similitud:.2f} < umbral {umbral:.2f}")
            )
            continue

        # 4. Salvaguarda de ambigüedad: dos candidatos casi iguales -> no decidimos.
        if similitud - segunda < MARGEN_AMBIGUEDAD and similitud < 1.0:
            sin_emparejar.append(
                SinEmparejar(
                    oficial.id,
                    oficial.apodo,
                    f"ambiguo: dos candidatos a {similitud:.2f} y {segunda:.2f}",
                )
            )
            continue

        emparejados.append(Emparejamiento(oficial.id, mejor, similitud))

    logger.info(
        "matching: %d emparejados, %d sin emparejar (umbral %.2f)",
        len(emparejados), len(sin_emparejar), umbral,
    )
    return emparejados, sin_emparejar
