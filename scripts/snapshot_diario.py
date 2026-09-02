#!/usr/bin/env python3
"""Snapshot diario del mercado (paso 9). Lo ejecuta el cron de Render.

Guardias, en este orden:
  1. **Hora**: no escribe nada antes de las 18:00 de Madrid, aunque el cron lo dispare.
     Es lo que evita capturar el mercado todavía abierto (R42).
  2. **Idempotencia**: si ya hay snapshot de hoy, no hace nada y sale con código 0 (R41).

Degradación: si el scraping falla, los valores **oficiales** se guardan igual, con la
analítica marcada como no disponible (R45).

Uso:
    uv run python scripts/snapshot_diario.py
    uv run python scripts/snapshot_diario.py --forzar-hora   # ignora la guardia horaria
    uv run python scripts/snapshot_diario.py --usuario x@y.z # limita a un usuario
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fantasy.analytics.servicio import (
    Analitica,
    componer,
    recolectar_analitica_de_bd,
    scrapear_todo_para_guardar,
)
from fantasy.storage.analitica_repo import guardar_analitica_del_dia, purgar_analitica_antigua
from fantasy.official.cliente import ClienteOficial
from fantasy.official.errores import ErrorAPIOficial
from fantasy.config import obtener_config
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.fechas import ahora_en_madrid, fecha_local, mercado_ya_cerro
from fantasy.storage.modelos import (
    CredencialesLaLiga,
    EstadoAnalitica,
    Jugador,
    SnapshotMercado,
    Usuario,
)
from fantasy.storage.retencion import purgar_snapshots_antiguos

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("snapshot_diario")


def _ya_hay_snapshot(sesion: Session, dia: date) -> bool:
    """Si ya existe el snapshot de mercado de ese dia.

    Solo cubre `market_snapshot`, no la analitica: son dos cosas con ritmos distintos.
    El mercado cambia una vez al dia (18:00) y no tiene sentido recapturarlo; la
    analitica de futbolfantasy se renueva tras el cambio de valor (00:00) y SI conviene
    refrescarla varias veces al dia.
    """
    return bool(
        sesion.scalar(
            select(func.count()).select_from(SnapshotMercado).where(SnapshotMercado.fecha == dia)
        )
    )


def _usuarios_con_credenciales(sesion: Session, email: str | None) -> list[Usuario]:
    """Solo usuarios que pueden autenticarse solos: el cron no puede pedir un token."""
    consulta = select(Usuario).join(CredencialesLaLiga, CredencialesLaLiga.user_id == Usuario.id)
    if email:
        consulta = consulta.where(Usuario.email == email)
    return list(sesion.scalars(consulta))


def _guardar(sesion: Session, dia: date, subastas, presentados) -> int:
    """Persiste el snapshot. Los datos oficiales entran aunque la analítica falte."""
    por_id = {p.id_oficial: p for p in presentados}
    escritos = 0

    for subasta in subastas:
        oficial = subasta.jugador
        if sesion.get(Jugador, oficial.id) is None:
            sesion.add(Jugador(
                id=oficial.id, nombre=oficial.nombre, apodo=oficial.apodo, slug=oficial.slug,
                equipo_id=oficial.equipo_id, posicion=oficial.posicion,
            ))
        sesion.flush()

        analitica = por_id[oficial.id].analitica if oficial.id in por_id else None
        disponible = bool(analitica and analitica.disponible)

        sesion.add(SnapshotMercado(
            player_id=oficial.id,
            fecha=dia,
            # --- oficial: se guarda siempre ---
            valor_mercado=oficial.valor_mercado,
            precio_venta=subasta.precio_venta,
            estado_jugador=oficial.estado,
            expira_en=subasta.expira_en,
            # --- analítico: puede faltar sin afectar a lo de arriba ---
            analitica_estado=EstadoAnalitica.DISPONIBLE if disponible else EstadoAnalitica.NO_DISPONIBLE,
            analitica_motivo=None if disponible else (analitica.motivo if analitica else "sin analítica"),
            analitica_origen=analitica.origen if disponible else None,
            analitica_capturado_en=analitica.capturado_en if disponible else None,
            tendencia_direccion=(
                analitica.tendencia_valor.direccion if disponible and analitica.tendencia_valor else None
            ),
            tendencia_variacion_euros=(
                analitica.tendencia_valor.variacion_euros if disponible and analitica.tendencia_valor else None
            ),
            probabilidad_jugar=(
                analitica.probabilidad_jugar.porcentaje if disponible and analitica.probabilidad_jugar else None
            ),
        ))
        escritos += 1

    sesion.commit()
    return escritos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--forzar-hora", action="store_true",
        help="Ignora la guardia de las 18:00 para el snapshot de mercado",
    )
    parser.add_argument("--usuario", default=None, help="Limita a un usuario concreto")
    args = parser.parse_args()

    ahora = ahora_en_madrid()
    dia = fecha_local(ahora)
    logger.info("ejecucion para %s (hora de Madrid: %s)", dia, ahora.strftime("%H:%M %Z"))

    fabrica = obtener_fabrica_sesiones()
    with fabrica() as sesion:
        usuarios = _usuarios_con_credenciales(sesion, args.usuario)
        if not usuarios:
            logger.warning(
                "ningun usuario con credenciales guardadas: el cron no puede autenticarse solo"
            )
            return

        # --- 1. ANALITICA: se actualiza SIEMPRE ---
        #
        # Es lo unico que consultan las pantallas, y se renueva tras el cambio de valor
        # de las 00:00. Por eso no lleva guardia horaria ni de idempotencia: el guardado
        # es un upsert, asi que ejecutarlo varias veces al dia solo mejora la frescura.
        tendencias, probabilidades = scrapear_todo_para_guardar()
        if tendencias:
            guardados = guardar_analitica_del_dia(sesion, dia, tendencias, probabilidades)
            logger.info("analitica diaria actualizada: %d jugadores", guardados)
        else:
            logger.warning("scraping caido: la analitica de hoy se queda como estaba")

        # --- 2. SNAPSHOT DE MERCADO: una vez al dia, tras el cierre ---
        #
        # Historico del mercado. A diferencia de la analitica, no tiene sentido
        # recapturarlo: el mercado no cambia hasta el cierre siguiente.
        if _ya_hay_snapshot(sesion, dia):
            logger.info("ya existe snapshot de mercado de %s; solo se actualizo la analitica", dia)
        elif not args.forzar_hora and not mercado_ya_cerro(ahora):
            logger.info(
                "el mercado aun no ha cerrado (18:00 Madrid): no se guarda snapshot, "
                "pero la analitica si se ha actualizado"
            )
        else:
            _guardar_snapshot_de_mercado(sesion, usuarios, dia)

        # --- 3. Purga de retencion ---
        borrados = purgar_snapshots_antiguos(sesion)
        borrados_analitica = purgar_analitica_antigua(
            sesion, dia, obtener_config().retencion_dias
        )
        if borrados or borrados_analitica:
            logger.info(
                "purga de retencion: %d snapshots y %d filas de analitica",
                borrados, borrados_analitica,
            )


def _guardar_snapshot_de_mercado(sesion: Session, usuarios: list[Usuario], dia: date) -> None:
    """Guarda la foto del mercado del dia. El mercado es el mismo para toda la liga, asi
    que basta con el primer usuario cuyo token funcione."""
    for usuario in usuarios:
        try:
            cliente = ClienteOficial(sesion, usuario.id)
            league_id, _ = cliente.obtener_liga_y_equipo()
            subastas = cliente.obtener_mercado(league_id)
        except ErrorAPIOficial as exc:
            logger.warning("no se pudo leer el mercado con un usuario: %s", exc)
            continue

        oficiales = [s.jugador for s in subastas]
        # Se compone leyendo de la BD, igual que hara la web: si esto sale bien, la web
        # tambien funcionara.
        analitica = recolectar_analitica_de_bd(sesion, oficiales)
        presentados = componer(oficiales, analitica)

        escritos = _guardar(sesion, dia, subastas, presentados)
        con_analitica = sum(1 for p in presentados if p.analitica.disponible)
        logger.info(
            "snapshot guardado: %d jugadores, %d con analitica, %d sin",
            escritos, con_analitica, escritos - con_analitica,
        )
        logger.info("matching: %d jugadores sin emparejar", analitica.sin_emparejar)
        return

    logger.error("ningun usuario pudo leer el mercado; no se ha guardado el snapshot")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
