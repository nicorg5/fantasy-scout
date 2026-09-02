"""Analítica diaria en base de datos: lo que permite que la web no scrapee."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from fantasy.analytics.presentacion import MediaSemanal, ProbabilidadJugar, TendenciaValor
from fantasy.scrapers.parsers import ProbabilidadScrapeada, TendenciaScrapeada
from fantasy.storage.analitica_repo import (
    a_probabilidad,
    a_tendencia,
    guardar_analitica_del_dia,
    leer_analitica_mas_reciente,
    purgar_analitica_antigua,
)
from fantasy.storage.modelos import AnaliticaDiaria

DIA = date(2099, 1, 1)   # fecha imposible: los tests no pueden pisar datos reales
AHORA = datetime.now(timezone.utc)


@pytest.fixture
def limpio(sesion_db):
    def _borrar():
        sesion_db.execute(delete(AnaliticaDiaria).where(AnaliticaDiaria.fecha >= date(2099, 1, 1)))
        sesion_db.commit()
    _borrar()
    yield sesion_db
    _borrar()


def _tendencia(id_ext="A", nombre="jugador uno", con_media=True):
    return TendenciaScrapeada(
        id_futbolfantasy=id_ext, nombre=nombre, equipo_externo="11",
        tendencia=TendenciaValor(direccion="sube", variacion_euros=5000),
        capturado_en=AHORA,
        media_semanal=MediaSemanal(media_diaria_euros=700, acumulado_euros=4900) if con_media else None,
    )


def test_guarda_todos_los_jugadores_no_solo_los_del_mercado(limpio):
    tendencias = [_tendencia(f"id{i}", f"jugador {i}") for i in range(50)]

    n = guardar_analitica_del_dia(limpio, DIA, tendencias, {})

    assert n == 50
    _, filas = leer_analitica_mas_reciente(limpio, DIA)
    assert len(filas) == 50


def test_reejecutar_el_mismo_dia_actualiza_en_vez_de_duplicar(limpio):
    """El cron dispara dos veces: no puede duplicar filas."""
    guardar_analitica_del_dia(limpio, DIA, [_tendencia()], {})
    guardar_analitica_del_dia(limpio, DIA, [_tendencia(nombre="nombre corregido")], {})

    _, filas = leer_analitica_mas_reciente(limpio, DIA)
    assert len(filas) == 1
    assert filas[0].nombre_externo == "nombre corregido"


def test_probabilidad_ausente_se_guarda_como_null(limpio):
    """462 de 669 jugadores tienen probabilidad: el resto NO puede guardarse como 0."""
    guardar_analitica_del_dia(limpio, DIA, [_tendencia()], {})

    _, filas = leer_analitica_mas_reciente(limpio, DIA)
    assert filas[0].probabilidad_jugar is None


def test_probabilidad_presente_se_guarda(limpio):
    prob = {"A": ProbabilidadScrapeada(id_futbolfantasy="A", slug="a", probabilidad=ProbabilidadJugar(porcentaje=80), capturado_en=AHORA)}

    guardar_analitica_del_dia(limpio, DIA, [_tendencia()], prob)

    _, filas = leer_analitica_mas_reciente(limpio, DIA)
    assert filas[0].probabilidad_jugar == 80


def test_no_se_sirven_datos_demasiado_viejos(limpio):
    """Mejor "no disponible" que analítica de la semana pasada como si fuera de hoy."""
    guardar_analitica_del_dia(limpio, DIA, [_tendencia()], {})

    fecha, filas = leer_analitica_mas_reciente(limpio, DIA + timedelta(days=30))

    assert fecha is None
    assert filas == []


def test_se_devuelve_la_fecha_para_poder_mostrarla(limpio):
    guardar_analitica_del_dia(limpio, DIA, [_tendencia()], {})

    fecha, _ = leer_analitica_mas_reciente(limpio, DIA)

    assert fecha == DIA


def test_reconstruccion_conserva_los_datos(limpio):
    prob = {"A": ProbabilidadScrapeada(id_futbolfantasy="A", slug="a", probabilidad=ProbabilidadJugar(porcentaje=65), capturado_en=AHORA)}
    guardar_analitica_del_dia(limpio, DIA, [_tendencia()], prob)

    _, filas = leer_analitica_mas_reciente(limpio, DIA)
    t = a_tendencia(filas[0])
    p = a_probabilidad(filas[0])

    assert t.tendencia.direccion == "sube"
    assert t.tendencia.variacion_euros == 5000
    assert t.media_semanal.media_diaria_euros == 700
    assert p.probabilidad.porcentaje == 65


def test_purga_respeta_la_retencion(sesion_db):
    """OJO: la purga borra TODO lo anterior al corte, no solo lo de este test.

    Por eso se usa una fecha ANTIGUA (1999) y un corte justo por encima: así el borrado
    no puede alcanzar datos reales, que siempre son posteriores. Con una fecha futura,
    este test destruía las filas que acababa de generar el cron.
    """
    antigua = date(1999, 1, 1)
    sesion_db.execute(delete(AnaliticaDiaria).where(AnaliticaDiaria.fecha == antigua))
    sesion_db.commit()
    guardar_analitica_del_dia(sesion_db, antigua, [_tendencia()], {})

    # Corte en 1999-01-02: alcanza la fila de prueba y nada mas.
    borrados = purgar_analitica_antigua(sesion_db, date(1999, 1, 2), retencion_dias=0)

    assert borrados == 1
    quedan = sesion_db.scalar(
        select(AnaliticaDiaria).where(AnaliticaDiaria.fecha == antigua)
    )
    assert quedan is None
