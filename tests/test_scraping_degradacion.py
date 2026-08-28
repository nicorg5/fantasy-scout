"""T4.6/R23: un fallo de red o de parseo nunca sube como excepción hacia la petición web."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.degradacion import obtener_probabilidades_equipo, obtener_tendencias_mercado


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


def test_equipo_caido_devuelve_lista_vacia_no_excepcion(cliente):
    with patch.object(cliente, "get_html_seguro", return_value=None):
        resultado = obtener_probabilidades_equipo(cliente, "malaga")

    assert resultado == []


def test_html_roto_tambien_degrada_sin_excepcion(cliente):
    """La red funciona, pero el sitio devolvió basura: mismo resultado, sin excepción."""
    with patch.object(cliente, "get_html_seguro", return_value="<html>sorpresa</html>"):
        resultado = obtener_tendencias_mercado(cliente)

    assert resultado == []
