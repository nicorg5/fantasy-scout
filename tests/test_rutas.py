"""Rutas de la app con datos reales (paso 7).

El servicio se mockea a propósito: estos tests comprueban el *comportamiento de las
rutas* (códigos, avisos, degradación), no la integración con LaLiga, que ya cubre
`test_official.py` con fixtures reales.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from fantasy.analytics.presentacion import (
    BloqueAnalitico,
    JugadorPresentado,
    ProbabilidadJugar,
    TendenciaValor,
)
from fantasy.official.errores import RespuestaInesperada, TokenInvalido

RUTAS = ("/plantilla", "/mercado")
DESTINO = {"/plantilla": "obtener_plantilla", "/mercado": "obtener_mercado"}


def _jugador(nombre: str = "Abel Bretones", con_analitica: bool = True) -> JugadorPresentado:
    if con_analitica:
        analitica = BloqueAnalitico.desde_scraping(
            tendencia_valor=TendenciaValor(direccion="sube", variacion_euros=42_000),
            probabilidad_jugar=ProbabilidadJugar(porcentaje=75),
            origen="futbolfantasy.com",
            capturado_en=datetime.now(timezone.utc),
        )
    else:
        analitica = BloqueAnalitico.no_disponible("sin datos hoy", origen="futbolfantasy.com")

    return JugadorPresentado(
        id_oficial="1", nombre=nombre, equipo="12", posicion="Defensa",
        valor_mercado_euros=5_000_000, analitica=analitica,
    )


def test_health(cliente):
    assert cliente.get("/health").json() == {"status": "ok"}


def test_raiz_redirige_a_plantilla(cliente):
    respuesta = cliente.get("/", follow_redirects=False)
    assert respuesta.status_code == 307
    assert respuesta.headers["location"] == "/plantilla"


@pytest.mark.parametrize("ruta", RUTAS)
def test_pantalla_muestra_jugadores_y_las_tres_metricas(cliente_autenticado, ruta):
    with patch(f"fantasy.api.app.{DESTINO[ruta]}", return_value=[_jugador()]):
        respuesta = cliente_autenticado.get(ruta)

    assert respuesta.status_code == 200
    html = respuesta.text
    assert "Abel Bretones" in html
    assert "Valor de mercado" in html
    assert "Tendencia de valor" in html
    assert "Probabilidad de jugar" in html


@pytest.mark.parametrize("ruta", RUTAS)
def test_analitica_no_disponible_sale_como_badge(cliente_autenticado, ruta):
    """R5: badge, no un 0 ni un guion mudo."""
    with patch(f"fantasy.api.app.{DESTINO[ruta]}", return_value=[_jugador(con_analitica=False)]):
        html = cliente_autenticado.get(ruta).text

    assert "analítica no disponible" in html


def test_origen_del_dato_etiquetado(cliente_autenticado):
    """R39: el origen es visible para el usuario, no solo en el payload."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador()]):
        html = cliente_autenticado.get("/plantilla").text

    assert "fuente: oficial" in html
    assert "fuente: futbolfantasy.com" in html


@pytest.mark.parametrize("ruta", RUTAS)
def test_token_invalido_avisa_y_no_revienta(cliente_autenticado, ruta):
    """R13: 200 con aviso y enlace a /token, nunca un 500."""
    with patch(f"fantasy.api.app.{DESTINO[ruta]}", side_effect=TokenInvalido("caducado")):
        respuesta = cliente_autenticado.get(ruta)

    assert respuesta.status_code == 200
    assert "caducado" in respuesta.text or "no es válido" in respuesta.text
    assert 'href="/token"' in respuesta.text


@pytest.mark.parametrize("ruta", RUTAS)
def test_fallo_de_laliga_avisa_y_no_revienta(cliente_autenticado, ruta):
    with patch(f"fantasy.api.app.{DESTINO[ruta]}", side_effect=RespuestaInesperada("x", "y")):
        respuesta = cliente_autenticado.get(ruta)

    assert respuesta.status_code == 200
    assert "LaLiga Fantasy" in respuesta.text


@pytest.mark.parametrize("ruta", RUTAS)
def test_rutas_protegidas_siguen_exigiendo_sesion(cliente, ruta):
    respuesta = cliente.get(ruta, follow_redirects=False)
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/login"


# --- htmx (T8.2 / R38) ---

@pytest.mark.parametrize(
    ("ruta", "destino"),
    [("/plantilla/tabla", "obtener_plantilla"), ("/mercado/tabla", "obtener_mercado")],
)
def test_fragmento_htmx_devuelve_solo_la_tabla(cliente_autenticado, ruta, destino):
    """R38: un fragmento, no una página entera; si no, se anidaría dentro de la actual."""
    with patch(f"fantasy.api.app.{destino}", return_value=[_jugador()]):
        respuesta = cliente_autenticado.get(ruta)

    assert respuesta.status_code == 200
    html = respuesta.text
    assert "Abel Bretones" in html
    assert "<table" in html
    assert "<!DOCTYPE html>" not in html, "es un fragmento, no una página"
    assert "<nav" not in html


@pytest.mark.parametrize("ruta", ["/plantilla/tabla", "/mercado/tabla"])
def test_fragmento_htmx_exige_sesion(cliente, ruta):
    respuesta = cliente.get(ruta, follow_redirects=False)
    assert respuesta.status_code == 302


def test_fragmento_con_error_sigue_siendo_fragmento(cliente_autenticado):
    """Un fallo no puede devolver la página completa dentro del hueco de la tabla."""
    with patch("fantasy.api.app.obtener_mercado", side_effect=TokenInvalido("caducado")):
        respuesta = cliente_autenticado.get("/mercado/tabla")

    assert respuesta.status_code == 200
    assert "<!DOCTYPE html>" not in respuesta.text
    assert "caducado" in respuesta.text.lower() or "sesión" in respuesta.text.lower()


def test_las_paginas_cargan_htmx_sin_build_step(cliente_autenticado):
    """R4/R37: htmx por CDN, sin bundler ni package.json."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador()]):
        html = cliente_autenticado.get("/plantilla").text

    assert "htmx.org" in html
    assert 'hx-get="/plantilla/tabla"' in html
