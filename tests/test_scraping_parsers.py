"""Bloque D — parsers de futbolfantasy.com (R22, R23).

Fixtures recortadas de HTML real capturado el 2026-08-27 (ver design.md §Fuente 2). Son
datos públicos de futbolistas, no personales, así que no necesitan anonimizar — solo
recortar tamaño.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fantasy.scrapers.parsers import parsear_probabilidad_equipo, parsear_tendencias_mercado

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def html_mercado() -> str:
    return (FIXTURES / "scraping_mercado.html").read_text(encoding="utf-8")


@pytest.fixture
def html_equipo() -> str:
    return (FIXTURES / "scraping_equipo.html").read_text(encoding="utf-8")


def test_tendencias_se_parsean_con_origen_y_timestamp(html_mercado):
    """R22: tendencia y probabilidad, cada una con origen y timestamp."""
    tendencias = parsear_tendencias_mercado(html_mercado)

    assert tendencias
    for t in tendencias:
        assert t.nombre
        assert t.equipo_externo, "sin equipo el matching pierde su salvaguarda principal"
        assert t.tendencia.direccion in ("sube", "baja", "estable")
        assert t.tendencia.variacion_euros >= 0
        assert t.capturado_en is not None


def test_incluye_el_caso_estable(html_mercado):
    """Una tendencia de 0€ debe leerse como 'estable', no como 'baja' con 0."""
    tendencias = parsear_tendencias_mercado(html_mercado)
    assert any(t.tendencia.direccion == "estable" for t in tendencias)


def test_probabilidades_se_parsean(html_equipo):
    probabilidades = parsear_probabilidad_equipo(html_equipo)

    assert probabilidades
    for p in probabilidades:
        assert p.slug
        assert 0 <= p.probabilidad.porcentaje <= 100
        assert p.capturado_en is not None


def test_slug_no_incluye_sufijo_de_temporada(html_equipo):
    """Bug real cazado con datos propios: el href a veces trae
    '.../jugadores/dani-sanchez/laliga-26-27'. El slug es solo el primer segmento."""
    probabilidades = parsear_probabilidad_equipo(html_equipo)
    assert any(p.slug == "dani-sanchez" for p in probabilidades)
    assert not any("/" in p.slug for p in probabilidades)


def test_mercado_sin_filas_no_lanza_devuelve_vacio():
    """R23: el sitio cambió -> lista vacía, no excepción."""
    resultado = parsear_tendencias_mercado("<html><body>nada que ver aquí</body></html>")
    assert resultado == []


def test_equipo_sin_jugadores_no_lanza_devuelve_vacio():
    resultado = parsear_probabilidad_equipo("<html><body>nada que ver aquí</body></html>")
    assert resultado == []


def test_fila_de_mercado_con_atributo_roto_se_salta_sin_tumbar_el_resto():
    """R23: una fila mutilada no debe abortar el parseo de las demás."""
    html = """
    <table><tbody>
      <tr class="elemento_jugador" data-id="1" data-nombre="roto" data-equipo="11"></tr>
      <tr class="elemento_jugador" data-id="2" data-nombre="bueno" data-equipo="11" data-diferencia1="1000"></tr>
    </tbody></table>
    """
    resultado = parsear_tendencias_mercado(html)
    assert len(resultado) == 1
    assert resultado[0].nombre == "bueno"


def test_jugador_de_equipo_con_atributo_roto_se_salta_sin_tumbar_el_resto():
    html = """
    <a class="camiseta" data-probabilidad="70%" href="/jugadores/sin-porcentaje-valido"></a>
    <a class="camiseta" data-probabilidad="abc" href="/jugadores/porcentaje-invalido"></a>
    """
    resultado = parsear_probabilidad_equipo(html)
    assert len(resultado) == 1
    assert resultado[0].slug == "sin-porcentaje-valido"


def test_media_semanal_es_la_diferencia7_dividida(html_mercado):
    """`data-diferencia7` es el ACUMULADO de la semana; la media diaria es /7.

    Confundir ambos multiplicaría por 7 lo que ve el usuario.
    """
    tendencias = [t for t in parsear_tendencias_mercado(html_mercado) if t.media_semanal]
    assert tendencias, "la fixture debe traer al menos un jugador con data-diferencia7"

    m = tendencias[0].media_semanal
    assert m.media_diaria_euros == round(m.acumulado_euros / 7)


def test_media_semanal_ausente_no_rompe_la_fila():
    """21 de 669 filas reales no traen data-diferencia7: deben parsearse igual."""
    html = """
    <table><tbody>
      <tr class="elemento_jugador" data-id="1" data-nombre="sin semana" data-equipo="11"
          data-diferencia1="1000"></tr>
    </tbody></table>
    """
    resultado = parsear_tendencias_mercado(html)

    assert len(resultado) == 1
    assert resultado[0].media_semanal is None
    assert resultado[0].tendencia is not None, "la tendencia diaria sí está y debe servirse"
