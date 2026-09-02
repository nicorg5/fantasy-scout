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
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from fantasy.analytics.presentacion import BloqueAnalitico, JugadorPresentado
from fantasy.matching.analitica import construir_bloque_analitico
from fantasy.matching.emparejador import CandidatoExterno, Emparejamiento, emparejar
from fantasy.matching.equipos import (
    cargar_mapa_equipos,
    equipo_futbolfantasy,
    nombre_equipo,
    slug_equipo_futbolfantasy,
)
from fantasy.matching.overrides import cargar_overrides
from fantasy.official.cliente import ClienteOficial
from fantasy.official.modelos import JugadorOficial
from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.degradacion import obtener_probabilidades_equipo, obtener_tendencias_mercado
from fantasy.storage.analitica_repo import (
    a_probabilidad,
    a_tendencia,
    leer_analitica_mas_reciente,
)
from fantasy.storage.fechas import fecha_local

logger = logging.getLogger("fantasy.analytics.servicio")

MOTIVO_SCRAPING_CAIDO = "el sitio de analítica no está disponible ahora mismo"


@dataclass(frozen=True)
class Analitica:
    """Lo que hace falta para decorar jugadores oficiales con analítica."""

    emparejamientos: dict[str, Emparejamiento]
    tendencias: dict
    probabilidades: dict
    # R27: una subida brusca de este numero avisa de que algo se rompio upstream.
    sin_emparejar: int = 0
    # Fecha de captura: la UI la muestra para que nadie confunda analitica de ayer con
    # analitica de ahora mismo.
    capturado_el: date | None = None

    @classmethod
    def vacia(cls) -> Analitica:
        return cls({}, {}, {})

    @property
    def hay_datos(self) -> bool:
        return bool(self.tendencias)


def recolectar_analitica_de_bd(sesion: Session, oficiales: list[JugadorOficial]) -> Analitica:
    """Lee la analítica ya guardada y empareja. **No toca la red.**

    Es lo que usan las pantallas: el scraping es serial y espaciado (~1 min para el
    mercado), así que hacerlo dentro de una petición HTTP hacía la web inservible. El
    cron lo deja guardado de noche y aquí solo se consulta y se empareja, que es cálculo
    puro.
    """
    fecha, filas = leer_analitica_mas_reciente(sesion, fecha_local())
    if not filas:
        logger.warning("no hay analítica guardada: se servirá como no disponible")
        return Analitica.vacia()

    candidatos = [
        CandidatoExterno(f.id_externo, f.nombre_externo, f.equipo_externo) for f in filas
    ]
    tendencias = {f.id_externo: a_tendencia(f) for f in filas}
    probabilidades = {
        f.id_externo: p for f in filas if (p := a_probabilidad(f)) is not None
    }

    emparejados, sin_emparejar = emparejar(oficiales, candidatos, overrides=cargar_overrides())
    if sin_emparejar:
        logger.info("jugadores sin emparejar: %d de %d", len(sin_emparejar), len(oficiales))

    return Analitica(
        {e.id_oficial: e for e in emparejados},
        tendencias,
        probabilidades,
        len(sin_emparejar),
        capturado_el=fecha,
    )


def recolectar_analitica(oficiales: list[JugadorOficial]) -> Analitica:
    """Scrapea EN VIVO y empareja. Solo la usa el cron: es lenta por diseño (rate limit).

    **Nunca lanza**: si algo falla, devuelve `Analitica.vacia()`."""
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
    return Analitica(
        {e.id_oficial: e for e in emparejados}, tendencias, probabilidades, len(sin_emparejar)
    )


def _probabilidades_de_los_equipos(
    cliente: ClienteScraping, oficiales: list[JugadorOficial], emparejados: list[Emparejamiento]
) -> dict:
    """La probabilidad vive en la página de cada equipo, así que hay que visitar una por
    equipo implicado. Son pocos (los del mercado del día) y el cliente ya serializa las
    peticiones con su rate limit.
    """
    # id_futbolfantasy -> slug: la página de equipo se direcciona por slug.
    slug_por_id_equipo = {
        id_ff: slug
        for o in oficiales
        if (id_ff := equipo_futbolfantasy(o.equipo_id))
        and (slug := slug_equipo_futbolfantasy(o.equipo_id))
    }
    por_id = _scrapear_probabilidades(cliente, slug_por_id_equipo.values())
    return {
        e.candidato.id_externo: por_id[e.candidato.id_externo]
        for e in emparejados
        if e.candidato.id_externo in por_id
    }


def _scrapear_probabilidades(cliente: ClienteScraping, slugs_equipo) -> dict[str, object]:
    """Descarga la página de cada equipo y devuelve las probabilidades indexadas por el
    id numérico de futbolfantasy — el MISMO que usa la tabla de mercado
    (`TendenciaScrapeada.id_futbolfantasy`).

    Antes esto cruzaba por NOMBRE con tolerancia a defectos del sitio (sufijos,
    similitud, apellido), porque la página de equipo solo daba un slug de texto. Se
    simplificó al descubrir (2026-09-02, con datos reales) que el widget correcto del
    "campo" ya expone el id numérico exacto en el propio HTML (`class="jugador_11830"`
    para Huijsen), lo que hace innecesaria cualquier comparación aproximada — y de paso
    corrige el bug real que tenía: el widget antiguo (`a.camiseta[data-probabilidad]`)
    solo cubría un subconjunto del equipo (20/26 verificado), dejando fuera a titulares
    habituales como Huijsen o Bellingham sin motivo aparente.

    Ids repetidos entre equipos no son un problema: son globales en el sitio, no por
    equipo, así que un dict plano basta.
    """
    por_id: dict[str, object] = {}
    for slug in slugs_equipo:
        for probabilidad in obtener_probabilidades_equipo(cliente, slug):
            por_id[probabilidad.id_futbolfantasy] = probabilidad
    return por_id


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


def fecha_de_la_analitica(sesion: Session) -> date | None:
    """De cuando son los datos analiticos que se estan sirviendo.

    La vista lo muestra: analitica de ayer presentada sin avisar seria enganosa, sobre
    todo cuando el mercado cambia cada dia a las 18:00.
    """
    fecha, _ = leer_analitica_mas_reciente(sesion, fecha_local())
    return fecha


def obtener_mercado(sesion: Session, usuario_id: uuid.UUID) -> list[JugadorPresentado]:
    """Mercado del día enriquecido.

    Si LaLiga falla, **propaga** (es la fuente de verdad y el usuario debe enterarse);
    si el scraping falla, **degrada**. Esa asimetría es deliberada.
    """
    cliente = ClienteOficial(sesion, usuario_id)
    league_id, _ = cliente.obtener_liga_y_equipo()
    subastas = cliente.obtener_mercado(league_id)
    oficiales = [s.jugador for s in subastas]
    return componer(oficiales, recolectar_analitica_de_bd(sesion, oficiales))


def obtener_plantilla(sesion: Session, usuario_id: uuid.UUID) -> list[JugadorPresentado]:
    cliente = ClienteOficial(sesion, usuario_id)
    league_id, team_id = cliente.obtener_liga_y_equipo()
    plantilla = cliente.obtener_plantilla(league_id, team_id)
    oficiales = [j.jugador for j in plantilla.jugadores]
    return componer(oficiales, recolectar_analitica_de_bd(sesion, oficiales))


def scrapear_todo_para_guardar(cliente: ClienteScraping | None = None):
    """Scrapea la analítica de TODOS los jugadores, para que el cron la persista.

    Distinto de `recolectar_analitica`, que solo resuelve los jugadores que hacen falta
    en ese momento: aquí se guarda todo lo que el sitio publica, porque la web necesitará
    después analítica de jugadores que no están en el mercado (los de cada plantilla).

    Devuelve (tendencias, probabilidades indexadas por id de futbolfantasy). Nunca lanza.
    """
    cliente = cliente or ClienteScraping()

    tendencias = obtener_tendencias_mercado(cliente)
    if not tendencias:
        logger.warning("scraping completo: sin tendencias, no hay nada que guardar")
        return [], {}

    # Una página por equipo, con el rate limit de por medio. Lento a propósito: esto
    # corre de noche y nadie espera. id_futbolfantasy -> slug, para poder pedir la URL.
    slug_por_id = {v["id"]: v["slug"] for v in cargar_mapa_equipos().values() if v.get("slug")}
    equipos_implicados = sorted({t.equipo_externo for t in tendencias if t.equipo_externo})
    slugs_equipo = []
    for id_equipo in equipos_implicados:
        slug = slug_por_id.get(id_equipo)
        if not slug:
            logger.info("equipo %s sin slug conocido: se omite su probabilidad", id_equipo)
            continue
        slugs_equipo.append(slug)

    # Cruce por id numérico exacto (ver `_scrapear_probabilidades`): ya no hace falta
    # comparar nombres.
    por_id = _scrapear_probabilidades(cliente, slugs_equipo)
    probabilidades = {
        t.id_futbolfantasy: por_id[t.id_futbolfantasy]
        for t in tendencias
        if t.id_futbolfantasy in por_id
    }

    logger.info(
        "scraping completo: %d jugadores, %d con probabilidad",
        len(tendencias), len(probabilidades),
    )
    return tendencias, probabilidades
