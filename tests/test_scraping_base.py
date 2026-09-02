"""Bloque D — reglas base de scraping (paso 4): robots.txt, rate limit, UA, caché."""

from __future__ import annotations

import dataclasses
import time
from unittest.mock import MagicMock, patch

import pytest

from fantasy.scrapers import cache
from fantasy.scrapers.cliente_http import ClienteScraping
from fantasy.scrapers.robots import RutaNoPermitida, _cache as robots_cache, verificar_permiso


@pytest.fixture(autouse=True)
def _limpiar_cache_robots():
    robots_cache.clear()
    yield
    robots_cache.clear()


@pytest.fixture
def cache_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DIR_CACHE", tmp_path)
    return tmp_path


def _robots_falso(permitido: bool):
    parser = MagicMock()
    parser.can_fetch.return_value = permitido
    return parser


def test_ruta_desautorizada_se_rechaza():
    """R18: se rechaza y la comprobación queda registrada (via logging, ver caplog)."""
    with patch("fantasy.scrapers.robots._parser_para", return_value=_robots_falso(False)):
        with pytest.raises(RutaNoPermitida):
            verificar_permiso("https://example.com/prohibido", "fantasy-scout/0.1 (+mailto:x@y.z)")


def test_ruta_autorizada_no_lanza(caplog):
    with patch("fantasy.scrapers.robots._parser_para", return_value=_robots_falso(True)):
        with caplog.at_level("INFO"):
            verificar_permiso("https://example.com/ok", "fantasy-scout/0.1 (+mailto:x@y.z)")

    assert any("permitido" in r.message for r in caplog.records)


def test_peticiones_espaciadas_en_el_tiempo(cache_tmp):
    """R19: nunca concurrente, espaciadas en segundos."""

    cliente = ClienteScraping()
    # obtener_config() está cacheada (singleton): se sustituye la referencia en esta
    # instancia por una copia, para no filtrar el cambio a otros tests.
    cliente._config = dataclasses.replace(cliente._config, scrape_intervalo_segundos=0.2)

    tiempos = []
    with patch("fantasy.scrapers.cliente_http.verificar_permiso"):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.text = "<html></html>"
            mock_get.return_value.raise_for_status = lambda: None

            for _ in range(3):
                tiempos.append(time.monotonic())
                cliente.get_html(f"https://example.com/{tiempos[-1]}")  # URL única: sin caché

    # El primer intervalo es ~0 porque no hay petición previa a la que esperar (correcto);
    # el rate limit se comprueba a partir del segundo.
    separaciones = [b - a for a, b in zip(tiempos, tiempos[1:])]
    assert all(s >= 0.19 for s in separaciones[1:]), separaciones


def test_user_agent_identifica_el_proyecto():
    from fantasy.config import obtener_config

    ua = obtener_config().user_agent
    assert "fantasy-scout" in ua
    assert "mailto:" in ua or "http" in ua  # forma de contacto presente


def test_cache_evita_segunda_peticion_de_red(cache_tmp):
    """R21: segunda ejecución dentro de la ventana de validez, 0 peticiones de red."""

    cliente = ClienteScraping()
    cliente._config = dataclasses.replace(cliente._config, scrape_intervalo_segundos=0)

    with patch("fantasy.scrapers.cliente_http.verificar_permiso") as mock_robots:
        with patch("httpx.get") as mock_get:
            mock_get.return_value.text = "<html>real</html>"
            mock_get.return_value.raise_for_status = lambda: None

            html1 = cliente.get_html("https://example.com/mismo-url")
            html2 = cliente.get_html("https://example.com/mismo-url")

    assert html1 == html2 == "<html>real</html>"
    assert mock_get.call_count == 1, "la segunda llamada debió venir de caché"
    assert mock_robots.call_count == 1, "sin red no hace falta comprobar robots.txt de nuevo"


def test_ignorar_cache_fuerza_peticion_real(cache_tmp):
    """Decisión del usuario (2026-09-02): el botón 'Actualizar datos' debe traer datos
    frescos de verdad, no la misma caché de 6h que usa el cron."""

    cliente = ClienteScraping()
    cliente._config = dataclasses.replace(cliente._config, scrape_intervalo_segundos=0)

    with patch("fantasy.scrapers.cliente_http.verificar_permiso"):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.text = "<html>real</html>"
            mock_get.return_value.raise_for_status = lambda: None

            cliente.get_html("https://example.com/mismo-url")
            cliente.get_html("https://example.com/mismo-url", ignorar_cache=True)

    assert mock_get.call_count == 2, "ignorar_cache debe saltarse el HIT de caché"


def test_ignorar_cache_sigue_guardando_para_llamadas_normales_posteriores(cache_tmp):
    """El resultado forzado en vivo se guarda igualmente en caché: una llamada normal
    justo después no debería volver a golpear la red."""

    cliente = ClienteScraping()
    cliente._config = dataclasses.replace(cliente._config, scrape_intervalo_segundos=0)

    with patch("fantasy.scrapers.cliente_http.verificar_permiso"):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.text = "<html>real</html>"
            mock_get.return_value.raise_for_status = lambda: None

            cliente.get_html("https://example.com/mismo-url", ignorar_cache=True)
            cliente.get_html("https://example.com/mismo-url")

    assert mock_get.call_count == 1
