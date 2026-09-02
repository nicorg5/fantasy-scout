"""Lectura y escritura de la analítica diaria.

Esta tabla es lo que permite que la web **no scrapee nunca**: el cron la rellena de noche
y las pantallas solo consultan. Ver design.md §Rendimiento.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantasy.analytics.presentacion import MediaSemanal, ProbabilidadJugar, TendenciaValor
from fantasy.scrapers.parsers import ProbabilidadScrapeada, TendenciaScrapeada
from fantasy.storage.modelos import AnaliticaDiaria

logger = logging.getLogger("fantasy.storage.analitica_repo")

# Días hacia atrás que se aceptan al servir. Si el cron lleva más de esto sin correr, es
# mejor decir "no disponible" que mostrar datos viejos como si fueran de hoy.
MAX_ANTIGUEDAD_DIAS = 3


def guardar_analitica_del_dia(
    sesion: Session,
    dia: date,
    tendencias: list[TendenciaScrapeada],
    probabilidades_por_id: dict[str, ProbabilidadScrapeada],
) -> int:
    """Guarda la analítica de TODOS los jugadores scrapeados.

    Idempotente: reejecutar el mismo día actualiza en vez de duplicar, para que el doble
    disparo del cron no rompa nada.
    """
    if not tendencias:
        logger.warning("sin tendencias que guardar para %s", dia)
        return 0

    filas = []
    for t in tendencias:
        prob = probabilidades_por_id.get(t.id_futbolfantasy)
        filas.append({
            "fecha": dia,
            "id_externo": t.id_futbolfantasy,
            "nombre_externo": t.nombre,
            "equipo_externo": t.equipo_externo,
            "tendencia_direccion": t.tendencia.direccion,
            "tendencia_variacion_euros": t.tendencia.variacion_euros,
            "media_diaria_euros": t.media_semanal.media_diaria_euros if t.media_semanal else None,
            "media_acumulada_euros": t.media_semanal.acumulado_euros if t.media_semanal else None,
            "probabilidad_jugar": prob.probabilidad.porcentaje if prob else None,
            "origen": "futbolfantasy.com",
            "capturado_en": t.capturado_en,
        })

    orden = insert(AnaliticaDiaria).values(filas)
    orden = orden.on_conflict_do_update(
        constraint="uq_analitica_diaria_fecha_jugador",
        set_={
            c: orden.excluded[c]
            for c in (
                "nombre_externo", "equipo_externo", "tendencia_direccion",
                "tendencia_variacion_euros", "media_diaria_euros", "media_acumulada_euros",
                "probabilidad_jugar", "capturado_en",
            )
        },
    )
    sesion.execute(orden)
    sesion.commit()
    logger.info("analítica guardada: %d jugadores para %s", len(filas), dia)
    return len(filas)


def leer_analitica_mas_reciente(sesion: Session, hoy: date) -> tuple[date | None, list[AnaliticaDiaria]]:
    """Devuelve (fecha del dato, filas) del día más reciente disponible.

    Se devuelve la fecha para que la UI pueda decir de cuándo son los datos: mostrar
    analítica de anteayer sin avisar sería engañoso.
    """
    corte = hoy - timedelta(days=MAX_ANTIGUEDAD_DIAS)
    fecha = sesion.scalar(
        select(AnaliticaDiaria.fecha)
        .where(AnaliticaDiaria.fecha >= corte)
        .order_by(AnaliticaDiaria.fecha.desc())
        .limit(1)
    )
    if fecha is None:
        logger.info("no hay analítica de los últimos %d días", MAX_ANTIGUEDAD_DIAS)
        return None, []

    filas = list(sesion.scalars(select(AnaliticaDiaria).where(AnaliticaDiaria.fecha == fecha)))
    return fecha, filas


def purgar_analitica_antigua(sesion: Session, hoy: date, retencion_dias: int) -> int:
    corte = hoy - timedelta(days=retencion_dias)
    borrados = sesion.execute(
        delete(AnaliticaDiaria).where(AnaliticaDiaria.fecha < corte)
    ).rowcount or 0
    sesion.commit()
    return borrados


def a_tendencia(fila: AnaliticaDiaria) -> TendenciaScrapeada:
    """Reconstruye el tipo del scraper desde la fila guardada, para que el resto del
    código no sepa si el dato viene de la red o de la base."""
    media = None
    if fila.media_diaria_euros is not None and fila.media_acumulada_euros is not None:
        media = MediaSemanal(
            media_diaria_euros=fila.media_diaria_euros,
            acumulado_euros=fila.media_acumulada_euros,
        )
    return TendenciaScrapeada(
        id_futbolfantasy=fila.id_externo,
        nombre=fila.nombre_externo,
        equipo_externo=fila.equipo_externo,
        tendencia=TendenciaValor(
            direccion=fila.tendencia_direccion or "estable",
            variacion_euros=fila.tendencia_variacion_euros or 0,
        ),
        capturado_en=fila.capturado_en,
        media_semanal=media,
    )


def a_probabilidad(fila: AnaliticaDiaria) -> ProbabilidadScrapeada | None:
    if fila.probabilidad_jugar is None:
        return None
    return ProbabilidadScrapeada(
        # `id_externo` YA es el id numérico de futbolfantasy (así se guarda desde
        # `guardar_analitica_del_dia`); no se guarda el slug aparte, así que aquí solo
        # es best-effort para trazabilidad.
        id_futbolfantasy=fila.id_externo,
        slug=fila.id_externo,
        probabilidad=ProbabilidadJugar(porcentaje=fila.probabilidad_jugar),
        capturado_en=fila.capturado_en,
    )
