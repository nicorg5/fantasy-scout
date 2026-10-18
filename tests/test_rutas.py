"""Rutas de la app con datos reales (paso 7).

El servicio se mockea a propósito: estos tests comprueban el *comportamiento de las
rutas* (códigos, avisos, degradación), no la integración con LaLiga, que ya cubre
`test_official.py` con fixtures reales.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from fantasy.analytics.presentacion import (
    BloqueAnalitico,
    JugadorPresentado,
    ProbabilidadJugar,
    ProteccionClausula,
    ProximoPartido,
    ProximoRival,
    TendenciaValor,
)
from fantasy.official.errores import CredencialesRechazadas, RespuestaInesperada, TokenInvalido

RUTAS = ("/plantilla", "/mercado")
DESTINO = {"/plantilla": "obtener_plantilla", "/mercado": "obtener_mercado"}


def _rival(rival: str | None = "Real Madrid", en_casa: bool = False) -> ProximoRival:
    if rival is None:
        return ProximoRival.sin_dato("sin datos del próximo partido de su equipo")
    return ProximoRival(
        partido=ProximoPartido(
            rival=rival, en_casa=en_casa, jornada=3,
            origen="futbolfantasy.com", capturado_en=datetime.now(timezone.utc),
        )
    )


def _jugador(
    nombre: str = "Abel Bretones",
    con_analitica: bool = True,
    rival: ProximoRival | None = None,
    proteccion: ProteccionClausula | None = None,
) -> JugadorPresentado:
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
        proximo_rival=rival or _rival(),
        proteccion_clausula=proteccion,
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
    """R13: 200 con aviso y enlace a /token, nunca un 500. Sin la palabra "token"
    (specs/registro, D10): quien llega por invitación no sabe qué es."""
    with patch(f"fantasy.api.app.{DESTINO[ruta]}", side_effect=TokenInvalido("caducado")):
        respuesta = cliente_autenticado.get(ruta)

    assert respuesta.status_code == 200
    assert "No hemos podido conectar con tu cuenta de LaLiga" in respuesta.text
    assert 'href="/token"' in respuesta.text


@pytest.mark.parametrize(
    ("ruta", "destino"),
    [
        ("/plantilla", "obtener_plantilla"),
        ("/plantilla/tabla", "obtener_plantilla"),
        ("/mercado", "obtener_mercado"),
        ("/clausulas", "obtener_clausulas"),
    ],
)
def test_contrasena_cambiada_en_laliga_lo_dice_claro(cliente_autenticado, ruta, destino):
    """specs/registro A20: 200, el motivo real y cómo arreglarlo."""
    with patch(f"fantasy.api.app.{destino}", side_effect=CredencialesRechazadas("x")):
        respuesta = cliente_autenticado.get(ruta)

    assert respuesta.status_code == 200
    assert "Tu contraseña de LaLiga ha cambiado" in respuesta.text
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


@pytest.mark.parametrize(
    ("ruta", "destino"),
    [("/plantilla/tabla", "obtener_plantilla"), ("/mercado/tabla", "obtener_mercado")],
)
def test_boton_actualizar_pide_analitica_en_vivo(cliente_autenticado, ruta, destino):
    """Decisión del usuario (2026-09-02): el botón 'Actualizar datos' debe volver a
    scrapear futbolfantasy.com, no solo releer la BD (a diferencia de la carga normal)."""
    with patch(f"fantasy.api.app.{destino}", return_value=[_jugador()]) as mock_obtener:
        cliente_autenticado.get(ruta)

    assert mock_obtener.call_args.kwargs.get("en_vivo") is True


@pytest.mark.parametrize("ruta", ["/plantilla", "/mercado"])
def test_la_carga_normal_de_la_pagina_no_pide_analitica_en_vivo(cliente_autenticado, ruta):
    """La carga normal sigue siendo instantánea: solo el botón fuerza el scrape."""
    destino = DESTINO[ruta]
    with patch(f"fantasy.api.app.{destino}", return_value=[_jugador()]) as mock_obtener:
        cliente_autenticado.get(ruta)

    assert mock_obtener.call_args.kwargs.get("en_vivo") is not True


@pytest.mark.parametrize(
    ("ruta", "destino"),
    [("/plantilla/tabla", "obtener_plantilla"), ("/mercado/tabla", "obtener_mercado")],
)
def test_boton_actualizar_refresca_la_fecha_via_oob(cliente_autenticado, ruta, destino):
    """El fragmento manda su propia actualización OOB del texto 'capturado el', porque
    tras un scrape en vivo la fecha del cron ya no describe lo que se muestra."""
    with patch(f"fantasy.api.app.{destino}", return_value=[_jugador()]):
        html = cliente_autenticado.get(ruta).text

    assert 'id="analitica-de"' in html
    assert 'hx-swap-oob="true"' in html


def test_fragmento_con_error_sigue_siendo_fragmento(cliente_autenticado):
    """Un fallo no puede devolver la página completa dentro del hueco de la tabla."""
    with patch("fantasy.api.app.obtener_mercado", side_effect=TokenInvalido("caducado")):
        respuesta = cliente_autenticado.get("/mercado/tabla")

    assert respuesta.status_code == 200
    assert "<!DOCTYPE html>" not in respuesta.text
    assert "No hemos podido conectar con tu cuenta de LaLiga" in respuesta.text
    assert 'href="/token"' in respuesta.text


def test_las_paginas_cargan_htmx_sin_build_step(cliente_autenticado):
    """R4/R37: htmx por CDN, sin bundler ni package.json."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador()]):
        html = cliente_autenticado.get("/plantilla").text

    assert "htmx.org" in html
    assert 'hx-get="/plantilla/tabla"' in html


def test_la_navegacion_entre_secciones_muestra_cargando(cliente_autenticado):
    """Decisión del usuario (2026-09-02): moverse entre secciones debe mostrar un aviso
    de carga. Se hace vía hx-boost, para no añadir JS propio (R4/R37)."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador()]):
        html = cliente_autenticado.get("/plantilla").text

    assert 'hx-boost="true"' in html
    assert 'id="cargando-nav"' in html


# --- variación total de la plantilla ---

def test_la_plantilla_muestra_la_variacion_total(cliente_autenticado):
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador()]):
        html = cliente_autenticado.get("/plantilla").text

    assert "variacion-total" in html
    assert "en las últimas 24 h" in html


def test_el_mercado_no_muestra_variacion(cliente_autenticado):
    """Es la variación de TU plantilla: en el mercado no significa nada."""
    with patch("fantasy.api.app.obtener_mercado", return_value=[_jugador()]):
        html = cliente_autenticado.get("/mercado").text

    assert "variacion-total" not in html


def test_el_fragmento_htmx_de_plantilla_trae_variacion_y_tabla_juntas(cliente_autenticado):
    """Van en un contenedor comun: si el swap sustituyera solo la tabla, el total
    quedaria desfasado; si la variacion fuera suelta, apareceria duplicada."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador()]):
        html = cliente_autenticado.get("/plantilla/tabla").text

    assert 'id="datos-jugadores"' in html
    assert "variacion-total" in html
    assert "<table" in html
    # Se cuenta el texto del bloque, no la clase: "variacion-total" aparece dos veces
    # dentro del mismo class (base + modificador) y daria un falso positivo.
    assert html.count("en las últimas 24 h") == 1, "no puede venir duplicada"


def test_el_fragmento_htmx_de_mercado_sigue_siendo_solo_la_tabla(cliente_autenticado):
    with patch("fantasy.api.app.obtener_mercado", return_value=[_jugador()]):
        html = cliente_autenticado.get("/mercado/tabla").text

    assert "variacion-total" not in html
    assert "<table" in html


# --- Próximo rival (specs/proximo-rival, T6) ---


def _celda_rival(html: str, nombre: str):
    """La celda "Próximo rival" de la fila de `nombre`: así el test comprueba que el
    rival va en SU fila, no en cualquier parte de la página."""
    from bs4 import BeautifulSoup

    for fila in BeautifulSoup(html, "html.parser").select("tbody tr"):
        if nombre in fila.get_text():
            return fila.select_one('td[data-etiqueta="Próximo rival"]')
    raise AssertionError(f"no hay fila para {nombre}")


@pytest.mark.parametrize("ruta", RUTAS)
def test_cada_fila_muestra_el_proximo_rival(cliente_autenticado, ruta):
    """P4 y P7: rival, Casa/Fuera escrito y su origen, en una celda con etiqueta para la
    vista en cards del móvil."""
    jugadores = [_jugador(), _jugador("Larrubia", rival=_rival("Levante", en_casa=True))]
    with patch(f"fantasy.api.app.{DESTINO[ruta]}", return_value=jugadores):
        html = cliente_autenticado.get(ruta).text

    assert '<th scope="col">Próximo rival</th>' in html
    abel = _celda_rival(html, "Abel Bretones").get_text(" ", strip=True)
    assert "vs Real Madrid" in abel and "Fuera" in abel
    assert "fuente: futbolfantasy.com" in abel
    larrubia = _celda_rival(html, "Larrubia").get_text(" ", strip=True)
    assert "vs Levante" in larrubia and "Casa" in larrubia


def test_la_columna_va_justo_despues_de_jugador(cliente_autenticado):
    """Decisión del usuario (2026-10-18)."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador()]):
        html = cliente_autenticado.get("/plantilla").text

    assert html.index(">Jugador</th>") < html.index(">Próximo rival</th>") < html.index(">Valor de mercado</th>")


@pytest.mark.parametrize("ruta", RUTAS)
def test_sin_rival_se_dice_sin_dato_con_motivo(cliente_autenticado, ruta):
    """P5: ni hueco, ni guion, ni rival inventado. El motivo va en el `title`."""
    with patch(f"fantasy.api.app.{DESTINO[ruta]}", return_value=[_jugador(rival=_rival(None))]):
        html = cliente_autenticado.get(ruta).text

    badge = _celda_rival(html, "Abel Bretones").select_one(".badge-no-disponible")
    assert badge.get_text(strip=True) == "sin dato"
    assert badge["title"] == "sin datos del próximo partido de su equipo"


def test_el_rival_no_depende_de_la_analitica_en_pantalla(cliente_autenticado):
    """P8: analítica no disponible y rival presente en la misma fila."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=[_jugador(con_analitica=False)]):
        html = cliente_autenticado.get("/plantilla").text

    assert "analítica no disponible" in html
    assert "vs Real Madrid" in _celda_rival(html, "Abel Bretones").get_text()


@pytest.mark.parametrize(
    ("ruta", "destino"),
    [("/plantilla/tabla", "obtener_plantilla"), ("/mercado/tabla", "obtener_mercado")],
)
def test_actualizar_datos_trae_el_rival_nuevo(cliente_autenticado, ruta, destino):
    """P6: el fragmento de "Actualizar datos" pinta el rival que trae la pasada en vivo."""
    nuevo = [_jugador(rival=_rival("Levante", en_casa=True))]
    with patch(f"fantasy.api.app.{destino}", return_value=nuevo) as obtener:
        html = cliente_autenticado.get(ruta).text

    assert obtener.call_args.kwargs.get("en_vivo") is True
    assert "vs Levante" in _celda_rival(html, "Abel Bretones").get_text()


# --- Protección de la cláusula (specs/clausula-plantilla, T4) ---

CABECERA_CLAUSULA = '<th scope="col">Cláusula</th>'


def _plantilla_con_protecciones() -> list[JugadorPresentado]:
    ahora = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

    def con(nombre, horas, blindado=False):
        hasta = None if horas is None else ahora + timedelta(hours=horas)
        return _jugador(
            nombre=nombre,
            proteccion=ProteccionClausula.desde_bloqueo(hasta, blindado, ahora),
        )

    return [
        con("Lejos", 25),
        con("Pronto", 23),
        con("Vencido", -1),
        con("Blindado", 5, blindado=True),
    ]


def _fila(html: str, nombre: str) -> str:
    """El <tr> del jugador, para no confundir lo de una fila con lo de otra."""
    inicio = html.index(nombre)
    return html[html.rindex("<tr", 0, inicio):html.index("</tr>", inicio)]


@pytest.mark.parametrize("ruta", ["/plantilla", "/plantilla/tabla"])
def test_plantilla_muestra_la_proteccion_de_cada_jugador(cliente_autenticado, ruta):
    """C2, C4, C7, C8."""
    with patch("fantasy.api.app.obtener_plantilla", return_value=_plantilla_con_protecciones()):
        html = cliente_autenticado.get(ruta).text

    assert CABECERA_CLAUSULA in html
    assert html.count('data-etiqueta="Cláusula"') == 4

    lejos = _fila(html, "Lejos")
    assert "protegido · 1d 1h" in lejos
    assert "clausula--" not in lejos

    pronto = _fila(html, "Pronto")
    assert "protegido · 23h 0m" in pronto
    assert "menos de 24 h" in pronto
    assert "clausula--pronto" in pronto

    vencido = _fila(html, "Vencido")
    assert "clausulable" in vencido
    assert "clausula--clausulable" in vencido

    blindado = _fila(html, "Blindado")
    assert "blindado" in blindado
    assert "clausula--" not in blindado


@pytest.mark.parametrize("ruta", ["/mercado", "/mercado/tabla"])
def test_el_mercado_no_tiene_columna_de_clausula(cliente_autenticado, ruta):
    """C6."""
    with patch("fantasy.api.app.obtener_mercado", return_value=[_jugador()]):
        html = cliente_autenticado.get(ruta).text

    assert "Abel Bretones" in html
    assert CABECERA_CLAUSULA not in html
    assert 'data-etiqueta="Cláusula"' not in html


@pytest.mark.parametrize("ruta", ["/plantilla", "/plantilla/tabla"])
def test_fecha_de_bloqueo_rota_avisa_y_no_revienta(cliente_autenticado, ruta):
    """C9: misma salida que cualquier otro fallo de la API oficial."""
    error = RespuestaInesperada("plantilla.players[0].buyoutClauseLockedEndTime", "fecha rota")
    with patch("fantasy.api.app.obtener_plantilla", side_effect=error):
        respuesta = cliente_autenticado.get(ruta)

    assert respuesta.status_code == 200
    assert "No se ha podido contactar con LaLiga Fantasy" in respuesta.text
