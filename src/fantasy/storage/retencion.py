"""Purga de snapshots antiguos.

La base de datos es una **caché operativa**, no la fuente de verdad del histórico: el
valor histórico de mercado es re-obtenible vía la API oficial o los sitios scrapeados
(ver design.md §Retención e histórico). Por eso se puede purgar sin drama.

La retención se configura con `FANTASY_RETENCION_DIAS` (90 por defecto).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from fantasy.config import obtener_config
from fantasy.storage.fechas import ahora_en_madrid, fecha_local
from fantasy.storage.modelos import EventoUso, SnapshotMercado

logger = logging.getLogger("fantasy.storage.retencion")


def purgar_snapshots_antiguos(
    sesion: Session, *, hoy: date | None = None, retencion_dias: int | None = None
) -> int:
    """Borra los snapshots más antiguos que la retención. Devuelve cuántos borró.

    Perezosa a propósito: la llama el job del snapshot diario, no un proceso aparte. Un
    cron menos que mantener, y si el job no corre tampoco importa que no se purgue.
    """
    hoy = hoy or fecha_local()
    dias = retencion_dias if retencion_dias is not None else obtener_config().retencion_dias
    corte = hoy - timedelta(days=dias)

    resultado = sesion.execute(delete(SnapshotMercado).where(SnapshotMercado.fecha < corte))
    borrados = resultado.rowcount or 0
    sesion.commit()

    if borrados:
        logger.info("purga de retención: %d snapshots anteriores a %s borrados", borrados, corte)
    return borrados


def purgar_eventos_uso(
    sesion: Session, *, ahora: datetime | None = None, retencion_dias: int | None = None
) -> int:
    """Borra los eventos de uso más antiguos que la retención (specs/observabilidad, O21).

    Misma retención que los snapshots, a propósito: una sola variable que entender. Aquí
    además es una decisión de privacidad, no solo de espacio: son datos de qué mira cada
    persona, y no hay motivo para guardarlos indefinidamente.
    """
    ahora = ahora or ahora_en_madrid()
    dias = retencion_dias if retencion_dias is not None else obtener_config().retencion_dias
    corte = ahora - timedelta(days=dias)

    resultado = sesion.execute(delete(EventoUso).where(EventoUso.creado_en < corte))
    borrados = resultado.rowcount or 0
    sesion.commit()

    if borrados:
        logger.info("purga de retención: %d eventos de uso anteriores a %s borrados", borrados, corte)
    return borrados
