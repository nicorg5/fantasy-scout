"""T1.3: el contrato de presentación admite analítica presente y `unavailable`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fantasy.analytics.presentacion import (
    BloqueAnalitico,
    JugadorPresentado,
    ProbabilidadJugar,
    TendenciaValor,
    formatear_euros,
)


def _jugador(analitica: BloqueAnalitico) -> JugadorPresentado:
    return JugadorPresentado(
        id_oficial="x-1",
        nombre="Jugador de prueba",
        equipo="Equipo",
        posicion="Delantero",
        valor_mercado_euros=1_000_000,
        analitica=analitica,
    )


def test_jugador_con_analitica_disponible_es_valido():
    jugador = _jugador(
        BloqueAnalitico.desde_scraping(
            tendencia_valor=TendenciaValor(direccion="sube", variacion_euros=50_000),
            probabilidad_jugar=ProbabilidadJugar(porcentaje=80),
            origen="futbolfantasy.com",
            capturado_en=datetime(2026, 8, 25, tzinfo=timezone.utc),
        )
    )

    assert jugador.analitica.disponible
    assert jugador.origen_valor == "oficial"
    assert jugador.analitica.origen == "futbolfantasy.com"


def test_jugador_con_analitica_no_disponible_es_valido():
    jugador = _jugador(BloqueAnalitico.no_disponible("el sitio no respondió"))

    assert not jugador.analitica.disponible
    assert jugador.analitica.motivo == "el sitio no respondió"
    # El dato oficial no se ve afectado por el fallo de la analítica.
    assert jugador.valor_mercado_euros == 1_000_000
    # Y no se inventan ceros que puedan confundirse con datos reales.
    assert jugador.analitica.tendencia_valor is None
    assert jugador.analitica.probabilidad_jugar is None


def test_analitica_no_disponible_exige_motivo():
    with pytest.raises(ValueError, match="motivo"):
        BloqueAnalitico(estado="unavailable")


def test_analitica_disponible_exige_al_menos_una_metrica_y_origen():
    with pytest.raises(ValueError, match="origen"):
        BloqueAnalitico(estado="disponible")


def test_analitica_disponible_sin_ninguna_metrica_es_invalida():
    from datetime import datetime, timezone

    with pytest.raises(ValueError, match="ninguna métrica"):
        BloqueAnalitico(
            estado="disponible", origen="x", capturado_en=datetime.now(timezone.utc)
        )


def test_metrica_ausente_debe_explicarse():
    """Un hueco sin motivo sería mudo: el usuario no sabría si falta o vale cero."""
    from datetime import datetime, timezone

    from fantasy.analytics.presentacion import TendenciaValor

    with pytest.raises(ValueError, match="por qué"):
        BloqueAnalitico(
            estado="disponible",
            tendencia_valor=TendenciaValor(direccion="sube", variacion_euros=1),
            origen="x",
            capturado_en=datetime.now(timezone.utc),
        )


def test_probabilidad_fuera_de_rango():
    with pytest.raises(ValueError):
        ProbabilidadJugar(porcentaje=140)


def test_formatear_euros():
    assert formatear_euros(1_234_567) == "1.234.567 €"
    assert formatear_euros(-68_000) == "-68.000 €"
