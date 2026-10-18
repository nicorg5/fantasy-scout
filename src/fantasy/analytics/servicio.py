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
from fantasy.matching.equipos import cargar_mapa_equipos, nombre_equipo
from fantasy.matching.overrides import cargar_overrides
from fantasy.official.cliente import ClienteOficial
from fantasy.official.modelos import JugadorOficial
from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.degradacion import obtener_pagina_equipo, obtener_tendencias_mercado
from fantasy.scrapers.parsers import PartidoScrapeado, ProbabilidadScrapeada, TendenciaScrapeada
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


def recolectar_analitica(oficiales: list[JugadorOficial], *, ignorar_cache: bool = False) -> Analitica:
    """Scrapea EN VIVO y empareja: la usa el cron, y también el botón "Actualizar datos"
    con `ignorar_cache=True` (2026-09-02) para que de verdad traiga datos frescos y no la
    misma caché de 6h que el cron. Es lenta por diseño (rate limit), pero acotada a los
    equipos de `oficiales`, no a toda la liga.

    **Nunca lanza**: si algo falla, devuelve `Analitica.vacia()`."""
    cliente = ClienteScraping()

    tendencias_lista = obtener_tendencias_mercado(cliente, ignorar_cache=ignorar_cache)
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

    # Las páginas de equipo se direccionan por SLUG (`/laliga/equipos/malaga`), no por el
    # id numérico: usar el número en la URL devuelve 404, pasó de verdad. El mapa da slug
    # y nombre por el id oficial del equipo; uno sin mapear se queda sin probabilidad ni
    # rival.
    mapa = cargar_mapa_equipos()
    equipos = {
        entrada["id"]: entrada
        for o in oficiales
        if (entrada := mapa.get(str(o.equipo_id))) and entrada.get("slug")
    }
    por_id, _partidos = _scrapear_paginas_de_equipo(
        cliente, equipos.values(), ignorar_cache=ignorar_cache
    )
    probabilidades = {
        e.candidato.id_externo: por_id[e.candidato.id_externo]
        for e in emparejados
        if e.candidato.id_externo in por_id
    }
    return Analitica(
        {e.id_oficial: e for e in emparejados}, tendencias, probabilidades, len(sin_emparejar)
    )


def _scrapear_paginas_de_equipo(
    cliente: ClienteScraping, equipos, *, ignorar_cache: bool = False
) -> tuple[dict[str, ProbabilidadScrapeada], dict[str, PartidoScrapeado]]:
    """Descarga la página de cada equipo (UNA petición por equipo, ver
    `obtener_pagina_equipo`) y devuelve:

    - las probabilidades indexadas por el id numérico de futbolfantasy del JUGADOR, el
      MISMO que usa la tabla de mercado (`TendenciaScrapeada.id_futbolfantasy`);
    - el próximo partido indexado por el id de futbolfantasy del EQUIPO.

    `equipos` son entradas del mapa de equipos (`id`, `nombre`, `slug`).

    Antes la probabilidad se cruzaba por NOMBRE con tolerancia a defectos del sitio
    (sufijos, similitud, apellido), porque la página de equipo solo daba un slug de
    texto. Se simplificó al descubrir (2026-09-02, con datos reales) que el widget
    correcto del "campo" ya expone el id numérico exacto en el propio HTML
    (`class="jugador_11830"` para Huijsen), lo que hace innecesaria cualquier comparación
    aproximada — y de paso corrige el bug real que tenía: el widget antiguo
    (`a.camiseta[data-probabilidad]`) solo cubría un subconjunto del equipo (20/26
    verificado), dejando fuera a titulares habituales como Huijsen o Bellingham sin motivo
    aparente.

    Ids repetidos entre equipos no son un problema: son globales en el sitio, no por
    equipo, así que un dict plano basta.
    """
    probabilidades: dict[str, ProbabilidadScrapeada] = {}
    partidos: dict[str, PartidoScrapeado] = {}
    for equipo in equipos:
        pagina = obtener_pagina_equipo(
            cliente, equipo["slug"], equipo["nombre"], ignorar_cache=ignorar_cache
        )
        for probabilidad in pagina.probabilidades:
            probabilidades[probabilidad.id_futbolfantasy] = probabilidad
        if pagina.partido is not None:
            partidos[equipo["id"]] = pagina.partido
    return probabilidades, partidos


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


def obtener_mercado(
    sesion: Session, usuario_id: uuid.UUID, en_vivo: bool = False
) -> list[JugadorPresentado]:
    """Mercado del día enriquecido.

    Si LaLiga falla, **propaga** (es la fuente de verdad y el usuario debe enterarse);
    si el scraping falla, **degrada**. Esa asimetría es deliberada.

    `en_vivo`: decisión del usuario (2026-09-02) para el botón "Actualizar datos" — vuelve
    a scrapear futbolfantasy.com en el momento, en vez de leer la analítica cacheada del
    cron. Solo lo pide `/mercado/tabla`; la carga normal de la página sigue siendo
    instantánea porque lee de BD.
    """
    cliente = ClienteOficial(sesion, usuario_id)
    league_id, _ = cliente.obtener_liga_y_equipo()
    subastas = cliente.obtener_mercado(league_id)
    oficiales = [s.jugador for s in subastas]
    analitica = (
        recolectar_analitica(oficiales, ignorar_cache=True)
        if en_vivo
        else recolectar_analitica_de_bd(sesion, oficiales)
    )
    return componer(oficiales, analitica)


def obtener_plantilla(
    sesion: Session, usuario_id: uuid.UUID, en_vivo: bool = False
) -> list[JugadorPresentado]:
    cliente = ClienteOficial(sesion, usuario_id)
    league_id, team_id = cliente.obtener_liga_y_equipo()
    plantilla = cliente.obtener_plantilla(league_id, team_id)
    oficiales = [j.jugador for j in plantilla.jugadores]
    analitica = (
        recolectar_analitica(oficiales, ignorar_cache=True)
        if en_vivo
        else recolectar_analitica_de_bd(sesion, oficiales)
    )
    return componer(oficiales, analitica)


@dataclass(frozen=True)
class ScrapingCompleto:
    """Lo que el cron scrapea para guardar. Un dataclass en vez de una tupla: con tres
    piezas, el orden posicional ya era una trampa."""

    tendencias: list[TendenciaScrapeada]
    # Por id de futbolfantasy del jugador.
    probabilidades: dict[str, ProbabilidadScrapeada]
    # Por id de futbolfantasy del equipo (specs/proximo-rival, D1).
    partidos: dict[str, PartidoScrapeado]

    @classmethod
    def vacio(cls) -> ScrapingCompleto:
        return cls([], {}, {})


def scrapear_todo_para_guardar(cliente: ClienteScraping | None = None) -> ScrapingCompleto:
    """Scrapea la analítica de TODOS los jugadores y el próximo partido de cada equipo,
    para que el cron lo persista.

    Distinto de `recolectar_analitica`, que solo resuelve los jugadores que hacen falta
    en ese momento: aquí se guarda todo lo que el sitio publica, porque la web necesitará
    después analítica de jugadores que no están en el mercado (los de cada plantilla).

    Nunca lanza.
    """
    cliente = cliente or ClienteScraping()

    tendencias = obtener_tendencias_mercado(cliente)
    if not tendencias:
        logger.warning("scraping completo: sin tendencias, no hay nada que guardar")
        return ScrapingCompleto.vacio()

    # Una página por equipo, con el rate limit de por medio. Lento a propósito: esto
    # corre de noche y nadie espera. El mapa se indexa por id oficial; aquí hace falta
    # por id de futbolfantasy, que es el que trae la tabla de mercado.
    por_id_ff = {v["id"]: v for v in cargar_mapa_equipos().values() if v.get("slug")}
    equipos_implicados = sorted({t.equipo_externo for t in tendencias if t.equipo_externo})
    equipos = []
    for id_equipo in equipos_implicados:
        if id_equipo not in por_id_ff:
            logger.info("equipo %s sin slug conocido: se omiten su probabilidad y su rival", id_equipo)
            continue
        equipos.append(por_id_ff[id_equipo])

    # Cruce por id numérico exacto (ver `_scrapear_paginas_de_equipo`): ya no hace falta
    # comparar nombres.
    por_id, partidos = _scrapear_paginas_de_equipo(cliente, equipos)
    probabilidades = {
        t.id_futbolfantasy: por_id[t.id_futbolfantasy]
        for t in tendencias
        if t.id_futbolfantasy in por_id
    }

    logger.info(
        "scraping completo: %d jugadores, %d con probabilidad, %d equipos con próximo partido",
        len(tendencias), len(probabilidades), len(partidos),
    )
    return ScrapingCompleto(tendencias, probabilidades, partidos)
