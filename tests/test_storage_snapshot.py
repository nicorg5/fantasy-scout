"""Bloque F — modelo de datos de snapshots (paso 6)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from fantasy.storage.fechas import fecha_local, mercado_ya_cerro
from fantasy.storage.modelos import EstadoAnalitica, Jugador, SnapshotMercado
from fantasy.storage.retencion import purgar_snapshots_antiguos

HOY = date(2026, 8, 27)


@pytest.fixture
def jugador(sesion_db):
    j = Jugador(
        id="test-9999", nombre="Jugador Prueba", apodo="Prueba", slug="jugador-prueba",
        equipo_id=12, posicion="Defensa",
    )
    sesion_db.add(j)
    sesion_db.commit()
    yield j
    sesion_db.query(SnapshotMercado).filter_by(player_id=j.id).delete()
    sesion_db.delete(sesion_db.get(Jugador, j.id))
    sesion_db.commit()


def _snapshot(player_id: str, fecha: date, **cambios) -> SnapshotMercado:
    base = dict(
        player_id=player_id, fecha=fecha, valor_mercado=1_000_000, precio_venta=1_100_000,
        estado_jugador="ok", expira_en=datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc),
        analitica_estado=EstadoAnalitica.NO_DISPONIBLE, analitica_motivo="sin datos",
    )
    base.update(cambios)
    return SnapshotMercado(**base)


def test_snapshot_guarda_oficial_y_analitica_por_separado(sesion_db, jugador):
    """R31: el bloque oficial y el analítico son secciones distintas."""
    sesion_db.add(_snapshot(
        jugador.id, HOY,
        analitica_estado=EstadoAnalitica.DISPONIBLE, analitica_motivo=None,
        analitica_origen="futbolfantasy.com",
        analitica_capturado_en=datetime.now(timezone.utc),
        tendencia_direccion="sube", tendencia_variacion_euros=50_000, probabilidad_jugar=80,
    ))
    sesion_db.commit()

    fila = sesion_db.scalar(select(SnapshotMercado).where(SnapshotMercado.player_id == jugador.id))
    assert fila.valor_mercado == 1_000_000           # oficial
    assert fila.probabilidad_jugar == 80             # analítico
    assert fila.analitica_estado is EstadoAnalitica.DISPONIBLE


def test_analitica_no_disponible_no_guarda_ceros(sesion_db, jugador):
    """La ausencia de analítica se representa con NULL + motivo, nunca con 0."""
    sesion_db.add(_snapshot(jugador.id, HOY))
    sesion_db.commit()

    fila = sesion_db.scalar(select(SnapshotMercado).where(SnapshotMercado.player_id == jugador.id))
    assert fila.analitica_estado is EstadoAnalitica.NO_DISPONIBLE
    assert fila.tendencia_variacion_euros is None, "un 0 se confundiría con 'no varió'"
    assert fila.probabilidad_jugar is None
    assert fila.analitica_motivo


def test_no_se_puede_duplicar_jugador_y_fecha(sesion_db, jugador):
    """R30: reejecutar el job el mismo día no puede duplicar filas."""
    sesion_db.add(_snapshot(jugador.id, HOY))
    sesion_db.commit()

    sesion_db.add(_snapshot(jugador.id, HOY, valor_mercado=2_000_000))
    with pytest.raises(IntegrityError):
        sesion_db.commit()
    sesion_db.rollback()

    total = sesion_db.scalar(
        select(func.count()).select_from(SnapshotMercado).where(SnapshotMercado.player_id == jugador.id)
    )
    assert total == 1


def test_purga_borra_lo_anterior_a_la_retencion(sesion_db, jugador):
    antiguo = HOY - timedelta(days=120)
    sesion_db.add_all([_snapshot(jugador.id, antiguo), _snapshot(jugador.id, HOY)])
    sesion_db.commit()

    borrados = purgar_snapshots_antiguos(sesion_db, hoy=HOY, retencion_dias=90)

    assert borrados == 1
    fechas = sesion_db.scalars(
        select(SnapshotMercado.fecha).where(SnapshotMercado.player_id == jugador.id)
    ).all()
    assert fechas == [HOY]


# --- fechas y guardia horaria ---

def test_fecha_local_usa_madrid_no_utc():
    """23:30 UTC del día 26 ya es día 27 en Madrid (verano)."""
    momento = datetime(2026, 8, 26, 23, 30, tzinfo=timezone.utc)
    assert fecha_local(momento) == date(2026, 8, 27)


def test_guardia_horaria_distingue_verano_de_invierno():
    """El mismo cron UTC cae antes o después del cierre según la estación: es justo el
    fallo silencioso que la guardia evita (ver design.md §Cron)."""
    verano = datetime(2026, 8, 27, 16, 30, tzinfo=timezone.utc)     # 18:30 Madrid (CEST)
    invierno = datetime(2026, 1, 27, 16, 30, tzinfo=timezone.utc)   # 17:30 Madrid (CET)

    assert mercado_ya_cerro(verano) is True
    assert mercado_ya_cerro(invierno) is False


def test_disparo_tardio_cubre_el_invierno():
    """Por eso hay dos disparos: el de 17:30 UTC sí pasa la guardia en invierno."""
    invierno_tarde = datetime(2026, 1, 27, 17, 30, tzinfo=timezone.utc)  # 18:30 Madrid
    assert mercado_ya_cerro(invierno_tarde) is True
