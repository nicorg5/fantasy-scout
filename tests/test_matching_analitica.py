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
        id_futbolfantasy=id_externo, slug=id_externo,
        probabilidad=ProbabilidadJugar(porcentaje=80), capturado_en=AHORA,
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


def test_cruce_de_probabilidades_es_por_id_exacto():
    """Desde 2026-09-02 el cruce entre la tabla de mercado y la página de equipo es por
    id numérico de futbolfantasy, no por nombre: ambas páginas del sitio usan el MISMO
    id (verificado con datos reales: `class="jugador_11830"` en la página de equipo es
    el mismo 11830 que `data-id` en la tabla de mercado). Ya no hace falta tolerar
    variantes de escritura para este cruce en absoluto.
    """
    from fantasy.analytics.servicio import _scrapear_probabilidades

    def cliente_falso(_cliente, slug):
        return [_probabilidad("11830")] if slug == "real-madrid" else []

    from unittest.mock import patch

    with patch(
        "fantasy.analytics.servicio.obtener_probabilidades_equipo", side_effect=cliente_falso
    ):
        por_id = _scrapear_probabilidades(cliente=None, slugs_equipo=["real-madrid"])

    assert set(por_id) == {"11830"}


def test_bug_real_de_cobertura_resuelto():
    """Caso real que motivó el cambio (2026-09-02): con el widget antiguo
    (`a.camiseta[data-probabilidad]`), Huijsen, Bellingham, Valverde y otros titulares
    quedaban fuera (20/26 del equipo cubiertos). El widget del 'campo'
    (`div.jugador_{id} > span.probabilidad-widget`) cubre la plantilla entera."""
    from fantasy.scrapers.parsers import parsear_probabilidad_equipo

    html = (
        '<div class="jugador_11830 tipo_campo campo camiseta-wrapper">'
        '<a class="jugador my-auto" href="https://www.futbolfantasy.com/jugadores/dean-huijsen"></a>'
        '<span class="probabilidad-widget"><span class="prob-3">80%</span></span>'
        "</div>"
    )
    resultado = parsear_probabilidad_equipo(html)

    assert len(resultado) == 1
    assert resultado[0].id_futbolfantasy == "11830"
    assert resultado[0].probabilidad.porcentaje == 80
