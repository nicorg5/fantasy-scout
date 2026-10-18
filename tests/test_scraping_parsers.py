"""Bloque D — parsers de futbolfantasy.com (R22, R23).

Fixtures recortadas de HTML real capturado el 2026-08-27 (ver design.md §Fuente 2). Son
datos públicos de futbolistas, no personales, así que no necesitan anonimizar — solo
recortar tamaño.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import logging

from fantasy.scrapers.parsers import (
    parsear_probabilidad_equipo,
    parsear_proximo_partido,
    parsear_tendencias_mercado,
)

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
        assert p.id_futbolfantasy
        assert p.slug
        assert 0 <= p.probabilidad.porcentaje <= 100
        assert p.capturado_en is not None


def test_id_futbolfantasy_es_el_dato_de_cruce_no_el_slug(html_equipo):
    """El id numérico (de `class="jugador_15344"`) es el mismo que usa la tabla de
    mercado: verificado con datos reales (2026-09-02), es un dato de identidad, no una
    aproximación por nombre."""
    probabilidades = parsear_probabilidad_equipo(html_equipo)
    assert all(p.id_futbolfantasy.isdigit() for p in probabilidades)
    assert len({p.id_futbolfantasy for p in probabilidades}) == len(probabilidades)


def test_no_incluye_sufijo_de_temporada_en_el_slug(html_equipo):
    """Bug real cazado con datos propios: el href a veces trae
    '.../jugadores/dani-lorenzo-1'. El slug es solo el primer segmento tras /jugadores/,
    tal cual viene — el sufijo pertenece al slug (desambigua homónimos en el sitio)."""
    probabilidades = parsear_probabilidad_equipo(html_equipo)
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
    """R23: un bloque sin span de probabilidad, o con texto no numérico, no debe abortar
    el parseo de los demás."""
    html = """
    <div class="jugador_1 tipo_campo campo camiseta-wrapper">
      <a class="jugador my-auto" href="https://www.futbolfantasy.com/jugadores/bueno"></a>
      <span class="probabilidad-widget"><span class="prob-3">70%</span></span>
    </div>
    <div class="jugador_2 tipo_campo campo camiseta-wrapper">
      <a class="jugador my-auto" href="https://www.futbolfantasy.com/jugadores/sin-widget"></a>
    </div>
    <div class="jugador_3 tipo_campo campo camiseta-wrapper">
      <a class="jugador my-auto" href="https://www.futbolfantasy.com/jugadores/no-numerico"></a>
      <span class="probabilidad-widget"><span class="prob-3">abc</span></span>
    </div>
    """
    resultado = parsear_probabilidad_equipo(html)
    assert len(resultado) == 1
    assert resultado[0].slug == "bueno"


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


# --- Próximo rival (specs/proximo-rival, P1/P2) ---


def _partido(local="Málaga", visitante="Sevilla", fase="Jornada 4", competicion="LaLiga") -> str:
    """Un `a.partido` con el mismo marcado que la sección real «Próximos partidos»."""
    return f"""
    <a class="ptheight partido nodeco" href="#">
      <div class="logo rslogo rsinfo"><img alt="{competicion}"/></div>
      <div class="equipo local"><img alt="{local}"/></div>
      <div class="info rsinfo"><div class="fase"> {fase} </div><div class="date"> Sab 24/10 </div></div>
      <div class="equipo visitante"><img alt="{visitante}"/></div>
    </a>"""


def _pagina(*partidos: str) -> str:
    return (
        '<section class="mod lista partidos proximos block-new">'
        + "".join(partidos)
        + "</section>"
    )


def test_proximo_partido_fuera_con_la_pagina_real(html_equipo):
    """P1: captura real del Málaga (2026-08-27), jornada 3 en el Bernabéu."""
    partido = parsear_proximo_partido(html_equipo, "Málaga")

    assert partido is not None
    assert partido.rival_nombre == "Real Madrid"
    assert partido.en_casa is False
    assert partido.jornada == 3
    assert partido.capturado_en is not None


def test_proximo_partido_en_casa():
    """El caso en casa se verificó con la página en vivo del 2026-10-18 (Málaga - Racing)."""
    partido = parsear_proximo_partido(_pagina(_partido(visitante="Racing", fase="Jornada 12")), "Málaga")

    assert partido is not None
    assert partido.en_casa is True
    assert partido.rival_nombre == "Racing"
    assert partido.jornada == 12


def test_la_copa_se_salta_y_cuenta_el_siguiente_de_liga():
    """Supuesto 6: solo LaLiga. Así venía la página en vivo del 2026-10-18."""
    html = _pagina(
        _partido(local="Sp. Alcázar", visitante="Málaga", fase="Ronda 1", competicion="Copa del Rey"),
        _partido(local="Betis", visitante="Málaga", fase="Jornada 11"),
    )
    partido = parsear_proximo_partido(html, "Málaga")

    assert partido is not None
    assert partido.rival_nombre == "Betis"
    assert partido.jornada == 11


def test_hace_falta_logo_de_liga_y_fase_de_jornada_a_la_vez():
    """Un partido rotulado "Jornada" pero con logo de Copa (o al revés) no es de liga."""
    html = _pagina(
        _partido(local="Copa A", fase="Jornada 1", competicion="Copa del Rey"),
        _partido(local="Copa B", fase="Ronda 1"),
        _partido(local="Betis", visitante="Málaga", fase="Jornada 11"),
    )
    assert parsear_proximo_partido(html, "Málaga").rival_nombre == "Betis"


def test_la_misma_pagina_no_sirve_para_otro_equipo(html_equipo):
    """La página es del Málaga: con otro nombre no hay forma segura de decidir."""
    assert parsear_proximo_partido(html_equipo, "Sevilla") is None


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>nada que ver aquí</body></html>",
        _pagina(),
        _pagina(_partido(fase="Ronda 1", competicion="Copa del Rey")),
        _pagina(_partido(visitante="")),
        # El primer partido de liga viene roto: no se salta al siguiente, que no es el próximo.
        _pagina(
            _partido().replace('<div class="equipo local"><img alt="Málaga"/></div>', ""),
            _partido(local="Betis", visitante="Málaga", fase="Jornada 5"),
        ),
    ],
    ids=["sin-seccion", "seccion-vacia", "solo-copa", "rival-vacio", "liga-mal-formado"],
)
def test_proximo_partido_raro_devuelve_none_sin_lanzar(html, caplog):
    """P2: cualquier forma inesperada es "sin dato", nunca una excepción ni un rival
    inventado."""
    with caplog.at_level(logging.INFO, logger="fantasy.scrapers.parsers"):
        assert parsear_proximo_partido(html, "Málaga") is None
    assert caplog.records, "el descarte tiene que quedar en el log"


def test_los_nombres_del_mapa_son_los_del_sitio(html_equipo):
    """Casa o fuera se decide comparando el `nombre` del mapa con el `alt` de los escudos.
    Si algún nombre del mapa no es el que usa el sitio, ese equipo se quedaría siempre sin
    rival. La fixture lleva la lista de equipos del menú del sitio, que usa los mismos
    nombres cortos ("Rayo", "Atlético") que los escudos."""
    from bs4 import BeautifulSoup

    from fantasy.matching.equipos import cargar_mapa_equipos

    soup = BeautifulSoup(html_equipo, "html.parser")
    del_sitio = {a["title"] for a in soup.select("a.team[title]")}

    nombres_del_mapa = {v["nombre"] for v in cargar_mapa_equipos().values()}
    assert nombres_del_mapa
    assert nombres_del_mapa <= del_sitio
