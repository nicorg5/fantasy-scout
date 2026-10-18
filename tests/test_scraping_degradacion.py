"""T4.6/R23: un fallo de red o de parseo nunca sube como excepción hacia la petición web."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.degradacion import obtener_pagina_equipo, obtener_tendencias_mercado


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    from fantasy.scrapers import cache

    monkeypatch.setattr(cache, "DIR_CACHE", tmp_path)
    return ClienteScraping()


@pytest.mark.parametrize(
    "excepcion",
    [
        httpx.ConnectError("no hay ruta al host"),
        httpx.TimeoutException("timeout"),
        httpx.HTTPStatusError("500", request=None, response=None),
    ],
)
def test_get_html_seguro_nunca_lanza(cliente, excepcion):
    with patch("fantasy.scrapers.cliente_http.verificar_permiso"):
        with patch("httpx.get", side_effect=excepcion):
            resultado = cliente.get_html_seguro("https://example.com/lo-que-sea")

    assert resultado is None


def test_mercado_caido_devuelve_lista_vacia_no_excepcion(cliente):
    with patch.object(cliente, "get_html_seguro", return_value=None) as mock:
        resultado = obtener_tendencias_mercado(cliente)

    assert resultado == []
    mock.assert_called_once()


def test_equipo_caido_devuelve_pagina_vacia_no_excepcion(cliente):
    with patch.object(cliente, "get_html_seguro", return_value=None):
        pagina = obtener_pagina_equipo(cliente, "malaga", "Málaga")

    assert pagina.probabilidades == []
    assert pagina.partido is None


def test_pagina_de_equipo_se_descarga_una_vez_para_los_dos_datos(cliente):
    """D2 (specs/proximo-rival): probabilidad y próximo partido salen de la MISMA
    descarga. Con `ignorar_cache=True` (botón "Actualizar datos") dos descargas serían
    dos peticiones reales al sitio."""
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "scraping_equipo.html").read_text(encoding="utf-8")
    with patch.object(cliente, "get_html_seguro", return_value=html) as descarga:
        pagina = obtener_pagina_equipo(cliente, "malaga", "Málaga", ignorar_cache=True)

    descarga.assert_called_once_with(
        "https://www.futbolfantasy.com/laliga/equipos/malaga", ignorar_cache=True
    )
    assert pagina.probabilidades
    assert pagina.partido is not None
    assert pagina.partido.rival_nombre == "Real Madrid"


def test_sin_partido_la_probabilidad_llega_igual(cliente):
    """P8: si la sección de partidos cambia, la probabilidad no se pierde."""
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "scraping_equipo.html").read_text(encoding="utf-8")
    html_sin_partidos = html.replace("partidos proximos", "partidos-que-ya-no-existen")
    with patch.object(cliente, "get_html_seguro", return_value=html_sin_partidos):
        pagina = obtener_pagina_equipo(cliente, "malaga", "Málaga")

    assert pagina.partido is None
    assert pagina.probabilidades


def test_html_roto_tambien_degrada_sin_excepcion(cliente):
    """La red funciona, pero el sitio devolvió basura: mismo resultado, sin excepción."""
    with patch.object(cliente, "get_html_seguro", return_value="<html>sorpresa</html>"):
        resultado = obtener_tendencias_mercado(cliente)

    assert resultado == []
