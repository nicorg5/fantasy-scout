"""Capa de servicio: compone datos oficiales con analítica scrapeada.

Es el punto donde las dos fuentes se juntan, y por tanto donde se hace cumplir la regla
dura de degradación: **si el scraping falla por completo, los datos oficiales se siguen
sirviendo igual**, con la analítica marcada como no disponible (R35, el requirement que
no se negocia).

Rellena el contrato de presentación fijado en el paso 1
(`fantasy.analytics.presentacion`), así que los templates no cambian al pasar de mock a
datos reales.
"""

from __future__ import annotations

import logging
import re
import uuid
from difflib import SequenceMatcher
from dataclasses import dataclass

from sqlalchemy.orm import Session

from fantasy.analytics.presentacion import BloqueAnalitico, JugadorPresentado
from fantasy.matching.analitica import construir_bloque_analitico
from fantasy.matching.emparejador import CandidatoExterno, Emparejamiento, emparejar
from fantasy.matching.equipos import (
    equipo_futbolfantasy,
    nombre_equipo,
    slug_equipo_futbolfantasy,
)
from fantasy.matching.overrides import cargar_overrides
from fantasy.official.cliente import ClienteOficial
from fantasy.official.modelos import JugadorOficial
from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.degradacion import obtener_probabilidades_equipo, obtener_tendencias_mercado

logger = logging.getLogger("fantasy.analytics.servicio")

MOTIVO_SCRAPING_CAIDO = "el sitio de analítica no está disponible ahora mismo"

# Umbral para cruzar el slug de la página de equipo con el nombre de la tabla de mercado.
# Alto a propósito: ambas formas salen del MISMO sitio y del MISMO equipo, así que una
# diferencia grande significaría que es otro jugador, no una variante de escritura.
UMBRAL_SLUG = 0.85


@dataclass(frozen=True)
class Analitica:
    """Lo que hace falta para decorar jugadores oficiales con analítica."""

    emparejamientos: dict[str, Emparejamiento]
    tendencias: dict
    probabilidades: dict

    @classmethod
    def vacia(cls) -> Analitica:
        return cls({}, {}, {})

    @property
    def hay_datos(self) -> bool:
        return bool(self.tendencias)


def recolectar_analitica(oficiales: list[JugadorOficial]) -> Analitica:
    """Scrapea y empareja. **Nunca lanza**: si algo falla, devuelve `Analitica.vacia()` y
    la composición producirá bloques 'no disponible' sin romper nada."""
    cliente = ClienteScraping()

    tendencias_lista = obtener_tendencias_mercado(cliente)
    if not tendencias_lista:
        logger.warning("sin tendencias de mercado: la analítica se servirá como no disponible")
        return Analitica.vacia()

    candidatos = [
        CandidatoExterno(t.id_futbolfantasy, t.nombre, t.equipo_externo)
        for t in tendencias_lista
    ]
    tendencias = {t.id_futbolfantasy: t for t in tendencias_lista}

    emparejados, sin_emparejar = emparejar(oficiales, candidatos, overrides=cargar_overrides())
    if sin_emparejar:
        # R27: este contador es la señal de que algo se rompió upstream.
        logger.info(
            "jugadores sin emparejar: %d de %d", len(sin_emparejar), len(oficiales)
        )

    probabilidades = _probabilidades_de_los_equipos(cliente, oficiales, emparejados)
    return Analitica({e.id_oficial: e for e in emparejados}, tendencias, probabilidades)


def _probabilidades_de_los_equipos(
    cliente: ClienteScraping, oficiales: list[JugadorOficial], emparejados: list[Emparejamiento]
) -> dict:
    """La probabilidad vive en la página de cada equipo, así que hay que visitar una por
    equipo implicado. Son pocos (los del mercado del día) y el cliente ya serializa las
    peticiones con su rate limit.

    Se indexa por id externo del jugador para que el consumidor no tenga que saber que
    vino de otra página que las tendencias.
    """
    # La página de equipo se direcciona por SLUG, no por el id numérico de `data-equipo`.
    equipos = {
        slug_equipo_futbolfantasy(o.equipo_id)
        for o in oficiales
        if slug_equipo_futbolfantasy(o.equipo_id)
    }
    por_slug: dict[str, object] = {}
    for equipo in sorted(e for e in equipos if e):
        for probabilidad in obtener_probabilidades_equipo(cliente, equipo):
            por_slug[probabilidad.slug] = probabilidad

    return _cruzar_por_slug(emparejados, por_slug)


def _sin_sufijo(slug: str) -> str:
    """'dani-lorenzo-1' -> 'dani-lorenzo'. El sufijo desambigua homónimos en el sitio."""
    return re.sub(r"-\d+$", "", slug)


def _cruzar_por_slug(emparejados: list[Emparejamiento], por_slug: dict) -> dict:
    """Cruza las dos páginas del mismo sitio, que **no comparten clave**: la tabla de
    mercado identifica al jugador con un id numérico y la de equipo con un slug.

    Se cruza por nombre normalizado, con tolerancia porque los slugs de futbolfantasy
    tienen defectos reales observados:
      - sufijo de desambiguación: 'dani-lorenzo-1'
      - **pierden la primera letra si va acentuada**: 'Ángel Ortiz' -> 'ngel-ortiz',
        'Álvaro Núñez' -> 'lvaro-nunez'
    Por eso no basta la igualdad exacta. El umbral es alto (0,85) porque aquí se comparan
    dos formas del *mismo* sitio: una diferencia grande significaría otro jugador.
    """
    from fantasy.matching.normalizacion import normalizar

    limpios = {_sin_sufijo(slug): valor for slug, valor in por_slug.items()}

    por_id_externo: dict[str, object] = {}
    for emparejamiento in emparejados:
        objetivo = normalizar(emparejamiento.candidato.nombre).replace(" ", "-")

        coincidencia = por_slug.get(objetivo) or limpios.get(objetivo)
        if coincidencia is None:
            mejor, similitud = None, 0.0
            for slug, valor in limpios.items():
                ratio = SequenceMatcher(None, objetivo, slug).ratio()
                if ratio > similitud:
                    mejor, similitud = valor, ratio
            if similitud >= UMBRAL_SLUG:
                coincidencia = mejor

        if coincidencia is not None:
            por_id_externo[emparejamiento.candidato.id_externo] = coincidencia
    return por_id_externo


def componer(oficiales: list[JugadorOficial], analitica: Analitica) -> list[JugadorPresentado]:
    """Une el bloque oficial con el analítico, jugador a jugador."""
    presentados = []
    for oficial in oficiales:
        if analitica.hay_datos:
            bloque = construir_bloque_analitico(
                oficial.id,
                analitica.emparejamientos,
                analitica.tendencias,
                analitica.probabilidades,
                posicion=oficial.posicion,
            )
        else:
            bloque = BloqueAnalitico.no_disponible(MOTIVO_SCRAPING_CAIDO)

        presentados.append(
            JugadorPresentado(
                id_oficial=oficial.id,
                nombre=oficial.apodo or oficial.nombre,
                # Nombre legible si lo conocemos; si no, el id, que al menos es
                # rastreable. Nunca vacío: un hueco sin explicación confunde más.
                equipo=nombre_equipo(oficial.equipo_id) or f"equipo {oficial.equipo_id}",
                posicion=oficial.posicion,
                valor_mercado_euros=oficial.valor_mercado,
                analitica=bloque,
            )
        )
    return presentados


def obtener_mercado(sesion: Session, usuario_id: uuid.UUID) -> list[JugadorPresentado]:
    """Mercado del día enriquecido.

    Si LaLiga falla, **propaga** (es la fuente de verdad y el usuario debe enterarse);
    si el scraping falla, **degrada**. Esa asimetría es deliberada.
    """
    cliente = ClienteOficial(sesion, usuario_id)
    league_id, _ = cliente.obtener_liga_y_equipo()
    subastas = cliente.obtener_mercado(league_id)
    oficiales = [s.jugador for s in subastas]
    return componer(oficiales, recolectar_analitica(oficiales))


def obtener_plantilla(sesion: Session, usuario_id: uuid.UUID) -> list[JugadorPresentado]:
    cliente = ClienteOficial(sesion, usuario_id)
    league_id, team_id = cliente.obtener_liga_y_equipo()
    plantilla = cliente.obtener_plantilla(league_id, team_id)
    oficiales = [j.jugador for j in plantilla.jugadores]
    return componer(oficiales, recolectar_analitica(oficiales))
