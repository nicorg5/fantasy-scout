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
    AnaliticaDiaria,
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
    """El trabajo del dia esta hecho solo si estan LAS DOS COSAS.

    Mirar unicamente `market_snapshot` no basta: si el snapshot existe pero falta la
    analitica, la web se queda sin datos hasta el dia siguiente. Paso de verdad en local
    y habria pasado igual en produccion.
    """
    hay_snapshot = bool(
        sesion.scalar(
            select(func.count()).select_from(SnapshotMercado).where(SnapshotMercado.fecha == dia)
        )
    )
    hay_analitica = bool(
        sesion.scalar(
            select(func.count()).select_from(AnaliticaDiaria).where(AnaliticaDiaria.fecha == dia)
        )
    )
    if hay_snapshot and not hay_analitica:
        logger.info("hay snapshot de %s pero falta la analitica: se regenera", dia)
    return hay_snapshot and hay_analitica


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
    parser.add_argument("--forzar-hora", action="store_true", help="Ignora la guardia de las 18:00")
    parser.add_argument("--usuario", default=None, help="Limita a un usuario concreto")
    args = parser.parse_args()

    ahora = ahora_en_madrid()
    dia = fecha_local(ahora)
    logger.info("snapshot para %s (hora de Madrid: %s)", dia, ahora.strftime("%H:%M %Z"))

    # Guardia 1: hora. Un disparo temprano no debe capturar el mercado abierto.
    if not args.forzar_hora and not mercado_ya_cerro(ahora):
        logger.info("el mercado aún no ha cerrado (18:00 Madrid); no se escribe nada")
        return

    fabrica = obtener_fabrica_sesiones()
    with fabrica() as sesion:
        # Guardia 2: idempotencia. Los dos disparos del cron son deliberados.
        if _ya_hay_snapshot(sesion, dia):
            logger.info("ya existe snapshot de %s; nada que hacer", dia)
            return

        usuarios = _usuarios_con_credenciales(sesion, args.usuario)
        if not usuarios:
            logger.warning(
                "ningún usuario con credenciales guardadas: el cron no puede autenticarse solo"
            )
            return

        # El mercado es el mismo para toda la liga, así que basta un usuario que funcione.
        for usuario in usuarios:
            try:
                cliente = ClienteOficial(sesion, usuario.id)
                league_id, _ = cliente.obtener_liga_y_equipo()
                subastas = cliente.obtener_mercado(league_id)
            except ErrorAPIOficial as exc:
                logger.warning("no se pudo leer el mercado con un usuario: %s", exc)
                continue

            oficiales = [s.jugador for s in subastas]

            # Se scrapea TODO el sitio (~669 jugadores), no solo los del mercado: la web
            # necesitara despues analitica de los jugadores de cada plantilla, que no
            # estan en subasta. Guardarlo aqui es lo que permite que la web no scrapee.
            tendencias, probabilidades = scrapear_todo_para_guardar()
            guardados = guardar_analitica_del_dia(sesion, dia, tendencias, probabilidades)
            logger.info("analitica diaria guardada: %d jugadores", guardados)

            # Se compone leyendo de la BD, igual que hara la web: si esto sale bien, la
            # web tambien funcionara.
            analitica = recolectar_analitica_de_bd(sesion, oficiales)
            presentados = componer(oficiales, analitica)

            escritos = _guardar(sesion, dia, subastas, presentados)
            con_analitica = sum(1 for p in presentados if p.analitica.disponible)
            logger.info(
                "snapshot guardado: %d jugadores, %d con analítica, %d sin",
                escritos, con_analitica, escritos - con_analitica,
            )
            logger.info("matching: %d jugadores sin emparejar", analitica.sin_emparejar)

            borrados = purgar_snapshots_antiguos(sesion)
            borrados_analitica = purgar_analitica_antigua(
                sesion, dia, obtener_config().retencion_dias
            )
            if borrados or borrados_analitica:
                logger.info(
                    "purga de retención: %d snapshots y %d filas de analítica",
                    borrados, borrados_analitica,
                )
            return

        logger.error("ningún usuario pudo leer el mercado; no se ha guardado nada")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
