"""Pantalla de clausulazos: orden, filtros y calculo del sobrepago."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from fantasy.analytics.clausulas import FiltrosClausulas, _clave_de_orden
from fantasy.analytics.presentacion import BloqueAnalitico, ClausulaPresentada
from fantasy.official.modelos import JugadorConClausula, JugadorOficial

AHORA = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _oficial(valor=1_000_000, posicion="Defensa"):
    return JugadorOficial(
        id="1", nombre="Jugador Uno", apodo="Uno", slug="jugador-uno", equipo_id=12,
        posicion=posicion, valor_mercado=valor, estado="ok", puntos=10, media_puntos=2.0,
    )


def _fila(nombre="X", clausula=2_000_000, valor=1_000_000, segundos=None,
          blindado=False, manager="M", equipo="Málaga", posicion="Defensa"):
    return ClausulaPresentada(
        id_oficial=nombre, nombre=nombre, manager=manager, equipo=equipo, posicion=posicion,
        valor_mercado_euros=valor, clausula_euros=clausula,
        sobrepago_euros=clausula - valor,
        sobrepago_pct=(clausula - valor) / valor * 100 if valor else None,
        segundos_para_desbloqueo=segundos, blindado=blindado,
        analitica=BloqueAnalitico.no_disponible("sin datos"),
    )


# --- fichable / espera ---

def test_jugador_con_bloqueo_vencido_es_fichable():
    item = JugadorConClausula(
        jugador=_oficial(), manager="M", manager_id="1", clausula=2_000_000,
        bloqueada_hasta=AHORA - timedelta(hours=5), blindado=False,
    )
    assert item.fichable(AHORA) is True
    assert item.segundos_para_desbloqueo(AHORA) is None


def test_jugador_blindado_no_es_fichable_aunque_no_este_bloqueado():
    """Blindar es una accion del manager: protege aunque el bloqueo haya vencido."""
    item = JugadorConClausula(
        jugador=_oficial(), manager="M", manager_id="1", clausula=2_000_000,
        bloqueada_hasta=None, blindado=True,
    )
    assert item.fichable(AHORA) is False


def test_tiempo_restante_se_calcula_desde_ahora():
    item = JugadorConClausula(
        jugador=_oficial(), manager="M", manager_id="1", clausula=2_000_000,
        bloqueada_hasta=AHORA + timedelta(hours=3), blindado=False,
    )
    assert item.segundos_para_desbloqueo(AHORA) == pytest.approx(3 * 3600)


# --- sobrepago ---

def test_sobrepago_en_euros_y_porcentaje():
    item = JugadorConClausula(
        jugador=_oficial(valor=1_000_000), manager="M", manager_id="1",
        clausula=1_500_000, bloqueada_hasta=None, blindado=False,
    )
    assert item.sobrepago_euros == 500_000
    assert item.sobrepago_pct == pytest.approx(50.0)


def test_sobrepago_pct_es_none_si_el_valor_es_cero():
    """Dividir daria infinito: un porcentaje sin sentido es peor que no mostrarlo."""
    item = JugadorConClausula(
        jugador=_oficial(valor=0), manager="M", manager_id="1",
        clausula=1_000_000, bloqueada_hasta=None, blindado=False,
    )
    assert item.sobrepago_pct is None


# --- orden ---

def test_los_fichables_van_siempre_primero():
    """Decision del usuario: primero lo que se puede fichar hoy."""
    filas = [
        _fila("bloqueado_barato", clausula=1_000_000, segundos=3600),
        _fila("fichable_caro", clausula=90_000_000),
    ]

    filas.sort(key=lambda f: _clave_de_orden(f, "clausula_asc"))

    assert filas[0].nombre == "fichable_caro", "un fichable caro va antes que un bloqueado barato"


def test_dentro_de_los_bloqueados_manda_el_tiempo():
    filas = [
        _fila("tarde", segundos=90_000),
        _fila("pronto", segundos=600),
    ]

    filas.sort(key=lambda f: _clave_de_orden(f, "desbloqueo"))

    assert [f.nombre for f in filas] == ["pronto", "tarde"]


def test_orden_por_clausula_descendente():
    filas = [_fila("barato", clausula=1_000_000), _fila("caro", clausula=50_000_000)]

    filas.sort(key=lambda f: _clave_de_orden(f, "clausula_desc"))

    assert filas[0].nombre == "caro"


# --- filtros ---

def test_orden_desconocido_no_rompe_la_pagina():
    """Un valor inventado en la URL cae al orden por defecto."""
    assert FiltrosClausulas.desde_query(orden="'; DROP TABLE").orden == "desbloqueo"


def test_cadenas_vacias_cuentan_como_sin_filtro():
    f = FiltrosClausulas.desde_query(manager="", posicion="   ", equipo=None)
    assert (f.manager, f.posicion, f.equipo) == (None, None, None)


# --- espera legible ---

@pytest.mark.parametrize(
    ("segundos", "blindado", "esperado"),
    [
        (None, False, "disponible"),
        (None, True, "blindado"),
        (2 * 86400 + 5 * 3600, False, "2d 5h"),
        (3 * 3600 + 20 * 60, False, "3h 20m"),
        (45 * 60, False, "45m"),
    ],
)
def test_espera_legible(segundos, blindado, esperado):
    """Un numero de segundos no dice nada de un vistazo."""
    assert _fila(segundos=segundos, blindado=blindado).espera_legible == esperado


# --- ruta ---

def _dos_filas():
    return (
        [_fila("Disponible", clausula=1_000_000), _fila("Bloqueado", segundos=7200)],
        {"managers": ["M"], "posiciones": ["Defensa"], "equipos": ["Málaga"]},
    )


def test_ruta_exige_sesion(cliente):
    respuesta = cliente.get("/clausulas", follow_redirects=False)
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/login"


def test_ruta_muestra_las_columnas_pedidas(cliente_autenticado):
    from unittest.mock import patch

    with patch("fantasy.api.app.obtener_clausulas", return_value=_dos_filas()):
        html = cliente_autenticado.get("/clausulas").text

    for columna in ("Manager", "Cláusula", "Sobrepago", "Valor de mercado",
                    "Tendencia de valor", "Media 7 días", "Probabilidad de jugar"):
        assert columna in html, f"falta la columna {columna}"


def test_ruta_ofrece_los_cuatro_filtros(cliente_autenticado):
    from unittest.mock import patch

    with patch("fantasy.api.app.obtener_clausulas", return_value=_dos_filas()):
        html = cliente_autenticado.get("/clausulas").text

    for campo in ('name="manager"', 'name="posicion"', 'name="equipo"', 'name="orden"'):
        assert campo in html, f"falta el filtro {campo}"


def test_los_filtros_de_la_url_se_conservan_en_el_formulario(cliente_autenticado):
    """Si al filtrar se perdiera la selección, no se sabría qué se está viendo."""
    from unittest.mock import patch

    with patch("fantasy.api.app.obtener_clausulas", return_value=_dos_filas()):
        html = cliente_autenticado.get("/clausulas?manager=M&orden=clausula_desc").text

    assert '<option value="M" selected>' in html.replace(" selected", " selected")
    assert 'value="clausula_desc" selected' in html


def test_token_caducado_avisa_sin_reventar(cliente_autenticado):
    from unittest.mock import patch

    from fantasy.official.errores import TokenInvalido

    with patch("fantasy.api.app.obtener_clausulas", side_effect=TokenInvalido("caducado")):
        respuesta = cliente_autenticado.get("/clausulas")

    assert respuesta.status_code == 200
    assert 'href="/token"' in respuesta.text


def test_sin_resultados_se_avisa_en_vez_de_mostrar_tabla_vacia(cliente_autenticado):
    from unittest.mock import patch

    vacio = ([], {"managers": ["M"], "posiciones": [], "equipos": []})
    with patch("fantasy.api.app.obtener_clausulas", return_value=vacio):
        html = cliente_autenticado.get("/clausulas?manager=M").text

    assert "Ningún jugador coincide" in html


# --- filtro por estado ---

def test_filtro_de_estado_solo_acepta_valores_conocidos():
    assert FiltrosClausulas.desde_query(estado="disponibles").estado == "disponibles"
    assert FiltrosClausulas.desde_query(estado="bloqueados").estado == "bloqueados"
    # Un valor inventado no filtra en vez de romper o de vaciar la lista.
    assert FiltrosClausulas.desde_query(estado="cualquier-cosa").estado is None
    assert FiltrosClausulas.desde_query(estado="").estado is None


def test_los_blindados_cuentan_como_bloqueados():
    """Para el usuario un blindado es lo mismo que un bloqueado: hoy no lo puede fichar,
    aunque el motivo técnico sea distinto."""
    blindado = _fila("blindado", segundos=None, blindado=True)

    assert blindado.fichable is False
    assert blindado.espera_legible == "blindado"


def test_ruta_ofrece_el_filtro_de_estado(cliente_autenticado):
    from unittest.mock import patch

    with patch("fantasy.api.app.obtener_clausulas", return_value=_dos_filas()):
        html = cliente_autenticado.get("/clausulas?estado=disponibles").text

    assert 'name="estado"' in html
    assert 'value="disponibles" selected' in html
