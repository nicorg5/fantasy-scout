"""Paso 9 — job del snapshot diario: guardias, idempotencia y degradación."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, select

from fantasy.analytics.presentacion import BloqueAnalitico, JugadorPresentado
from fantasy.storage.modelos import EstadoAnalitica, Jugador, SnapshotMercado

# El job es un script, no un módulo del paquete: se carga por ruta.
_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "snapshot_diario.py"
_spec = importlib.util.spec_from_file_location("snapshot_diario", _RUTA)
snapshot_diario = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapshot_diario)

# Fecha deliberadamente imposible: los tests NO pueden borrar snapshots reales al
# limpiar. Usar la fecha de hoy hacía que la suite destruyera datos del usuario.
HOY = date(2099, 1, 1)


class _SubastaFalsa:
    def __init__(self, jugador):
        self.jugador = jugador
        self.precio_venta = 1_100_000
        self.expira_en = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


class _OficialFalso:
    def __init__(self):
        self.id = "test-snap-1"
        self.nombre = "Jugador Snapshot"
        self.apodo = "Snapshot"
        self.slug = "jugador-snapshot"
        self.equipo_id = 12
        self.posicion = "Defensa"
        self.valor_mercado = 1_000_000
        self.estado = "ok"


@pytest.fixture
def limpio(sesion_db):
    """Deja la fecha de prueba sin snapshots, antes y después."""
    def _borrar():
        sesion_db.execute(delete(SnapshotMercado).where(SnapshotMercado.fecha == HOY))
        sesion_db.execute(delete(Jugador).where(Jugador.id == "test-snap-1"))
        sesion_db.commit()

    _borrar()
    yield sesion_db
    _borrar()


def _presentado(con_analitica: bool) -> JugadorPresentado:
    if con_analitica:
        from fantasy.analytics.presentacion import ProbabilidadJugar, TendenciaValor
        bloque = BloqueAnalitico.desde_scraping(
            tendencia_valor=TendenciaValor(direccion="sube", variacion_euros=5000),
            probabilidad_jugar=ProbabilidadJugar(porcentaje=60),
            origen="futbolfantasy.com",
            capturado_en=datetime.now(timezone.utc),
        )
    else:
        bloque = BloqueAnalitico.no_disponible("scraping caído", origen="futbolfantasy.com")

    return JugadorPresentado(
        id_oficial="test-snap-1", nombre="Snapshot", equipo="Málaga", posicion="Defensa",
        valor_mercado_euros=1_000_000, analitica=bloque,
    )


def test_scraping_caido_guarda_igual_los_valores_oficiales(limpio):
    """**R45**: la analítica es prescindible; el dato oficial no."""
    oficial = _OficialFalso()

    escritos = snapshot_diario._guardar(
        limpio, HOY, [_SubastaFalsa(oficial)], [_presentado(con_analitica=False)]
    )

    assert escritos == 1
    fila = limpio.scalar(select(SnapshotMercado).where(SnapshotMercado.fecha == HOY))
    assert fila.valor_mercado == 1_000_000, "el valor oficial se guarda igual"
    assert fila.precio_venta == 1_100_000
    assert fila.analitica_estado is EstadoAnalitica.NO_DISPONIBLE
    assert fila.analitica_motivo
    # Y nunca un 0 que se confunda con "no varió".
    assert fila.tendencia_variacion_euros is None
    assert fila.probabilidad_jugar is None


def test_con_analitica_se_guardan_ambos_bloques(limpio):
    oficial = _OficialFalso()

    snapshot_diario._guardar(limpio, HOY, [_SubastaFalsa(oficial)], [_presentado(True)])

    fila = limpio.scalar(select(SnapshotMercado).where(SnapshotMercado.fecha == HOY))
    assert fila.valor_mercado == 1_000_000
    assert fila.analitica_estado is EstadoAnalitica.DISPONIBLE
    assert fila.probabilidad_jugar == 60
    assert fila.tendencia_variacion_euros == 5000


def test_deteccion_de_snapshot_existente(limpio):
    """R41: base de la idempotencia del cron."""
    assert snapshot_diario._ya_hay_snapshot(limpio, HOY) is False

    snapshot_diario._guardar(limpio, HOY, [_SubastaFalsa(_OficialFalso())], [_presentado(True)])

    assert snapshot_diario._ya_hay_snapshot(limpio, HOY) is True


@pytest.mark.parametrize(
    ("momento_utc", "debe_escribir"),
    [
        (datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc), False),  # 17:00 Madrid (CEST)
        (datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc), True),   # 18:30 Madrid (CEST)
        (datetime(2026, 1, 28, 16, 30, tzinfo=timezone.utc), False),  # 17:30 Madrid (CET)
        (datetime(2026, 1, 28, 17, 30, tzinfo=timezone.utc), True),   # 18:30 Madrid (CET)
    ],
)
def test_guardia_horaria_segun_la_estacion(momento_utc, debe_escribir):
    """R42: el mismo cron UTC cae antes o después del cierre según sea verano o invierno.

    Ejecuta `main()` de verdad: comprueba que un disparo temprano ni siquiera llega a
    consultar usuarios, que es lo que garantiza que no escribe nada.
    """
    with patch.object(snapshot_diario.sys, "argv", ["snapshot_diario.py"]):
        with patch.object(snapshot_diario, "ahora_en_madrid", return_value=momento_utc):
            with patch.object(snapshot_diario, "_ya_hay_snapshot", return_value=False):
                with patch.object(
                    snapshot_diario, "_usuarios_con_credenciales", return_value=[]
                ) as usuarios:
                    snapshot_diario.main()

    assert usuarios.called is debe_escribir


def test_solo_se_usan_usuarios_con_credenciales(sesion_db, usuario_de_prueba):
    """El cron no puede pedir un token a nadie: sin credenciales, ese usuario no sirve."""
    usuario, _ = usuario_de_prueba

    encontrados = snapshot_diario._usuarios_con_credenciales(sesion_db, usuario.email)

    assert encontrados == [], "sin credenciales guardadas no debe seleccionarse"
