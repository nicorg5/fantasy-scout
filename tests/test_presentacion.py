"""T1.3: el contrato de presentación admite analítica presente y `unavailable`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fantasy.analytics.presentacion import (
    BloqueAnalitico,
    JugadorPresentado,
    ProbabilidadJugar,
    ProximoPartido,
    ProximoRival,
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
        proximo_rival=ProximoRival.sin_dato("sin datos"),
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


# --- variación total de la plantilla ---

def _presentado(direccion: str, euros: int, con_analitica: bool = True):
    from datetime import datetime, timezone

    from fantasy.analytics.presentacion import (
        BloqueAnalitico, JugadorPresentado, TendenciaValor,
    )

    if con_analitica:
        bloque = BloqueAnalitico.desde_scraping(
            tendencia_valor=TendenciaValor(direccion=direccion, variacion_euros=euros),
            origen="futbolfantasy.com",
            capturado_en=datetime.now(timezone.utc),
            motivo_probabilidad="sin dato",
        )
    else:
        bloque = BloqueAnalitico.no_disponible("sin datos")

    return JugadorPresentado(
        id_oficial="x", nombre="X", equipo="Málaga", posicion="Defensa",
        valor_mercado_euros=1_000_000, analitica=bloque,
        proximo_rival=ProximoRival.sin_dato("sin datos"),
    )


def test_variacion_aplica_el_signo_de_cada_tendencia():
    """`variacion_euros` es siempre positivo; el signo vive en `direccion`. Sumar sin
    aplicarlo daría un total que solo sube."""
    from fantasy.analytics.presentacion import calcular_variacion

    v = calcular_variacion([
        _presentado("sube", 100_000),
        _presentado("baja", 30_000),
        _presentado("estable", 0),
    ])

    assert v.euros == 70_000
    assert v.direccion == "sube"


def test_variacion_negativa_se_marca_como_bajada():
    from fantasy.analytics.presentacion import calcular_variacion

    v = calcular_variacion([_presentado("baja", 50_000), _presentado("sube", 10_000)])

    assert v.euros == -40_000
    assert v.direccion == "baja"
    assert v.simbolo == "▼"


def test_sin_ningun_dato_no_se_inventa_un_cero():
    """Mostrar '0 €' sería mentir: no es que no haya variado, es que no lo sabemos."""
    from fantasy.analytics.presentacion import calcular_variacion

    assert calcular_variacion([_presentado("sube", 0, con_analitica=False)]) is None
    assert calcular_variacion([]) is None


def test_variacion_parcial_se_marca_como_incompleta():
    from fantasy.analytics.presentacion import calcular_variacion

    v = calcular_variacion([
        _presentado("sube", 100_000),
        _presentado("sube", 0, con_analitica=False),
    ])

    assert v.jugadores_con_dato == 1
    assert v.jugadores_totales == 2
    assert v.completa is False


# --- Próximo rival (specs/proximo-rival, P5) ---


def _partido_presentado(en_casa=False):
    return ProximoPartido(
        rival="Real Madrid", en_casa=en_casa, jornada=3,
        origen="futbolfantasy.com", capturado_en=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"motivo": "   "}, "ambos"],
    ids=["ni-partido-ni-motivo", "motivo-vacio", "partido-y-motivo"],
)
def test_proximo_rival_sin_dato_exige_motivo(kwargs):
    """P5: la invariante vive en el tipo. Un hueco sin explicar no se puede construir."""
    if kwargs == "ambos":
        kwargs = {"partido": _partido_presentado(), "motivo": "sobra"}
    with pytest.raises(ValueError):
        ProximoRival(**kwargs)


def test_proximo_rival_con_partido_o_sin_dato():
    assert ProximoRival(partido=_partido_presentado()).partido.rival == "Real Madrid"
    assert ProximoRival.sin_dato("porque sí").motivo == "porque sí"


def test_lado_del_partido_es_texto_legible():
    assert _partido_presentado(en_casa=True).lado == "Casa"
    assert _partido_presentado(en_casa=False).lado == "Fuera"
