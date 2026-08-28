"""T5.6/R26: sin match => analítica no disponible, nunca datos de otro jugador."""

from __future__ import annotations

from datetime import datetime, timezone

from fantasy.analytics.presentacion import ProbabilidadJugar, TendenciaValor
from fantasy.matching.analitica import construir_bloque_analitico
from fantasy.matching.emparejador import CandidatoExterno, Emparejamiento
from fantasy.scrapers.parsers import ProbabilidadScrapeada, TendenciaScrapeada

AHORA = datetime.now(timezone.utc)


def _tendencia(id_externo: str) -> TendenciaScrapeada:
    return TendenciaScrapeada(
        id_futbolfantasy=id_externo, nombre="x", equipo_externo="11",
        tendencia=TendenciaValor(direccion="sube", variacion_euros=1000), capturado_en=AHORA,
    )


def _probabilidad(id_externo: str) -> ProbabilidadScrapeada:
    return ProbabilidadScrapeada(
        slug=id_externo, probabilidad=ProbabilidadJugar(porcentaje=80), capturado_en=AHORA
    )


def test_jugador_emparejado_recibe_su_analitica():
    emparejamientos = {"1": Emparejamiento("1", CandidatoExterno("A", "x", "11"), 1.0)}

    bloque = construir_bloque_analitico("1", emparejamientos, {"A": _tendencia("A")}, {"A": _probabilidad("A")})

    assert bloque.disponible
    assert bloque.probabilidad_jugar.porcentaje == 80


def test_sin_emparejar_es_no_disponible_con_motivo():
    bloque = construir_bloque_analitico("999", {}, {"A": _tendencia("A")}, {"A": _probabilidad("A")})

    assert not bloque.disponible
    assert bloque.motivo
    assert bloque.tendencia_valor is None
    assert bloque.probabilidad_jugar is None


def test_no_se_cuelan_datos_de_otro_jugador():
    """El fallo catastrófico que esto previene: emparejado a 'A' pero solo hay datos de 'B'."""
    emparejamientos = {"1": Emparejamiento("1", CandidatoExterno("A", "x", "11"), 1.0)}

    bloque = construir_bloque_analitico("1", emparejamientos, {"B": _tendencia("B")}, {"B": _probabilidad("B")})

    assert not bloque.disponible


def test_tendencia_sin_probabilidad_se_sirve_igualmente():
    """Decisión del usuario (2026-08-27): futbolfantasy solo publica probabilidad para el
    once probable. Descartar la tendencia por eso perdía datos reales de 4 de 11."""
    emparejamientos = {"1": Emparejamiento("1", CandidatoExterno("A", "x", "11"), 1.0)}

    bloque = construir_bloque_analitico("1", emparejamientos, {"A": _tendencia("A")}, {})

    assert bloque.disponible
    assert bloque.tendencia_valor is not None
    assert bloque.probabilidad_jugar is None
    assert bloque.motivo_probabilidad, "el hueco debe explicarse, no quedar mudo"
    assert not bloque.completa


def test_entrenador_no_cuenta_como_fallo_de_matching():
    """Quedan fuera del MVP por decisión del usuario: el motivo debe decir eso, no
    'sin emparejar', que haría pensar en un problema del matching."""
    bloque = construir_bloque_analitico("1", {}, {}, {}, posicion="Entrenador")

    assert not bloque.disponible
    assert "entrenadores" in bloque.motivo


def test_cruce_de_slug_tolera_los_defectos_reales_de_futbolfantasy():
    """Los slugs del sitio pierden la primera letra si va acentuada ('Álvaro Núñez' ->
    'lvaro-nunez') y a veces llevan sufijo ('dani-lorenzo-1'). Casos reales."""
    from fantasy.analytics.servicio import _cruzar_por_slug
    from fantasy.matching.emparejador import CandidatoExterno, Emparejamiento

    emparejados = [
        Emparejamiento("1", CandidatoExterno("A", "alvaro nuñez", "5"), 1.0),
        Emparejamiento("2", CandidatoExterno("B", "dani lorenzo", "11"), 1.0),
    ]
    por_slug = {
        "lvaro-nunez": _probabilidad("lvaro-nunez"),   # primera letra perdida
        "dani-lorenzo-1": _probabilidad("dani-lorenzo-1"),  # sufijo de desambiguación
    }

    resultado = _cruzar_por_slug(emparejados, por_slug)

    assert set(resultado) == {"A", "B"}


def test_cruce_de_slug_no_empareja_a_un_jugador_distinto():
    from fantasy.analytics.servicio import _cruzar_por_slug
    from fantasy.matching.emparejador import CandidatoExterno, Emparejamiento

    emparejados = [Emparejamiento("1", CandidatoExterno("A", "igor galdin", "10"), 1.0)]
    por_slug = {"victor-garcia": _probabilidad("victor-garcia")}

    assert _cruzar_por_slug(emparejados, por_slug) == {}
