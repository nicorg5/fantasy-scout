"""Observabilidad: registro de uso (specs/observabilidad).

Los dos primeros tests son el **spike de verificación** (T2): comprueban los supuestos de
Starlette de los que depende `design.md §D3` ANTES de construir el middleware encima. Se
montan apps de juguete a propósito: lo que está en duda es el comportamiento del
framework, no el código del proyecto.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from starlette.background import BackgroundTask

from fantasy.observabilidad import registro
from fantasy.observabilidad.registro import clasificar, debe_registrarse, registrar_evento
from fantasy.storage.modelos import EventoUso, TipoEvento


def test_spike_background_task_de_middleware_corre_tras_construir_la_respuesta():
    """D3: el registro se adjunta a `response.background` y corre AL FINAL.

    Lo que se verifica es el **orden**: la tarea se ejecuta después de que el middleware
    haya devuelto la respuesta, no en medio de la petición.

    OJO con lo que este test NO demuestra: `TestClient` espera a que las background tasks
    terminen antes de devolver el control, así que de aquí no se puede concluir nada sobre
    la latencia que percibe un cliente real. Eso se mide contra un uvicorn de verdad.
    """
    orden: list[str] = []
    app = FastAPI()

    @app.middleware("http")
    async def registrar(request: Request, call_next):
        respuesta = await call_next(request)
        respuesta.background = BackgroundTask(lambda: orden.append("tarea"))
        orden.append("middleware-devuelve-respuesta")
        return respuesta

    @app.get("/algo")
    def algo():
        orden.append("ruta")
        return {"ok": True}

    respuesta = TestClient(app).get("/algo")

    assert respuesta.status_code == 200
    assert orden == ["ruta", "middleware-devuelve-respuesta", "tarea"]


def test_spike_el_middleware_ve_el_302_que_genera_un_exception_handler():
    """O9: una petición sin sesión se registra con su 302, no se pierde.

    En la app real, `NoAutenticado` la convierte en redirección un `exception_handler`
    registrado en `app.py`. La duda era si el middleware ve ese 302 o una excepción sin
    manejar: los handlers de excepción de Starlette corren POR DENTRO de los middlewares
    de usuario, así que debería ver la respuesta ya convertida.
    """
    vistos: list[int] = []
    app = FastAPI()

    class NoAutenticadoFalso(Exception):
        """Réplica de `fantasy.auth.dependencias.NoAutenticado`."""

    @app.exception_handler(NoAutenticadoFalso)
    def a_login(request: Request, exc: NoAutenticadoFalso) -> RedirectResponse:
        return RedirectResponse(url="/login", status_code=302)

    @app.middleware("http")
    async def registrar(request: Request, call_next):
        respuesta = await call_next(request)
        vistos.append(respuesta.status_code)
        return respuesta

    @app.get("/protegida")
    def protegida():
        raise NoAutenticadoFalso()

    respuesta = TestClient(app).get("/protegida", follow_redirects=False)

    assert respuesta.status_code == 302
    assert vistos == [302], "el middleware no vio el 302: habría que replantear D3/O9"


# --------------------------------------------------------------------------------------
# T3: clasificación y emisión
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ruta, tiene_query, esperado",
    [
        ("/plantilla", False, (TipoEvento.NAVEGACION, None)),
        ("/mercado", False, (TipoEvento.NAVEGACION, None)),
        # El botón "Actualizar datos": es la única razón de ser de estas rutas.
        ("/plantilla/tabla", False, (TipoEvento.ACCION, "actualizar_datos")),
        ("/mercado/tabla", False, (TipoEvento.ACCION, "actualizar_datos")),
        # La misma ruta significa dos cosas distintas según lleve filtros o no.
        ("/clausulas", False, (TipoEvento.NAVEGACION, None)),
        ("/clausulas", True, (TipoEvento.ACCION, "filtrar_clausulas")),
        # Una ruta que esta spec no conoce nunca inventa una acción (O4).
        ("/algo/futuro", True, (TipoEvento.NAVEGACION, None)),
    ],
)
def test_clasificar(ruta, tiene_query, esperado):
    assert clasificar(ruta, tiene_query=tiene_query) == esperado


@pytest.mark.parametrize(
    "ruta, esperado",
    [
        ("/plantilla", True),
        ("/health", False),
        ("/favicon.ico", False),
        # El panel vive dentro de la app: sin excluirlo, mirarlo ensuciaría lo que muestra.
        ("/uso", False),
        ("/static/estilos.css", False),
    ],
)
def test_debe_registrarse(ruta, esperado):
    assert debe_registrarse(ruta) is esperado


def test_registrar_evento_no_propaga_errores(monkeypatch, caplog):
    """O6: perder telemetría es un fastidio; tumbar una respuesta por ella, un bug."""

    def _explota():
        raise RuntimeError("base de datos caída")

    monkeypatch.setattr(registro, "obtener_fabrica_sesiones", _explota)

    registrar_evento(ruta="/plantilla", estado_http=200, duracion_ms=12)  # no debe lanzar

    assert "no se pudo registrar el evento de uso" in caplog.text


# --------------------------------------------------------------------------------------
# T4: el middleware sobre la app real
# --------------------------------------------------------------------------------------


@pytest.fixture
def eventos(sesion_db):
    """Vacía `evento_uso` antes del test para poder afirmar cuentas exactas.

    Se limpia al entrar y no al salir: así, si un test falla, las filas que dejó siguen
    ahí para poder mirarlas. Es la BD local, que los tests ya crean y borran a voluntad.
    """
    sesion_db.execute(delete(EventoUso))
    sesion_db.commit()

    def _leer(ruta: str | None = None) -> list[EventoUso]:
        sesion_db.expire_all()
        consulta = select(EventoUso).order_by(EventoUso.creado_en)
        if ruta is not None:
            consulta = consulta.where(EventoUso.ruta == ruta)
        return list(sesion_db.scalars(consulta))

    return _leer


def test_una_navegacion_deja_exactamente_una_fila(cliente_autenticado, eventos):
    """O2. `/token` sirve para esto porque no llama a LaLiga ni scrapea nada.

    Vale igual para la navegación por `hx-boost`: un enlace potenciado hace una GET normal
    a la misma ruta, así que el servidor no distingue —ni necesita distinguir— entre
    pinchar en el menú y recargar la página.
    """
    assert cliente_autenticado.get("/token").status_code == 200

    filas = eventos("/token")
    assert len(filas) == 1
    assert filas[0].tipo == TipoEvento.NAVEGACION
    assert filas[0].accion is None
    assert filas[0].estado_http == 200
    assert filas[0].user_id is not None


def test_las_rutas_de_infraestructura_no_dejan_rastro(cliente, eventos):
    """O3: `/health` lo llama el healthcheck de Render cada pocos segundos; si se
    registrara, ahogaría el uso real de las personas."""
    for _ in range(10):
        cliente.get("/health")
    # Sin seguir la redirección: sin sesión, /uso manda a /login, y esa visita a /login SÍ
    # es uso legítimo que se registra. Aquí solo interesa que /uso no deje rastro.
    cliente.get("/uso", follow_redirects=False)
    cliente.get("/static/estilos.css")

    assert [(e.ruta, e.estado_http, e.user_id) for e in eventos()] == []


def test_peticion_sin_sesion_se_registra_sin_usuario(cliente, eventos):
    """O9: se guarda el 302 hacia /login, sin inventarse un usuario."""
    respuesta = cliente.get("/plantilla", follow_redirects=False)
    assert respuesta.status_code == 302

    filas = eventos("/plantilla")
    assert len(filas) == 1
    assert filas[0].user_id is None
    assert filas[0].estado_http == 302


def test_el_boton_de_actualizar_se_registra_como_accion(cliente_autenticado, eventos):
    """O4. Se parchea el servicio porque lo que se prueba es el registro, no el scraping."""
    from unittest.mock import patch

    with patch("fantasy.api.app.obtener_plantilla", return_value=[]):
        assert cliente_autenticado.get("/plantilla/tabla").status_code == 200

    filas = eventos("/plantilla/tabla")
    assert len(filas) == 1
    assert filas[0].tipo == TipoEvento.ACCION
    assert filas[0].accion == "actualizar_datos"


def test_filtrar_clausulas_se_registra_como_accion(cliente_autenticado, eventos):
    """O4: la misma ruta, dos significados, distinguidos por la query string."""
    from unittest.mock import patch

    vacio = ([], {"managers": [], "posiciones": [], "equipos": []})
    with patch("fantasy.api.app.obtener_clausulas", return_value=vacio):
        assert cliente_autenticado.get("/clausulas?posicion=Defensa").status_code == 200

    filas = eventos("/clausulas")
    assert len(filas) == 1
    assert filas[0].accion == "filtrar_clausulas"


def test_la_duracion_se_mide_y_la_ruta_no_lleva_la_query(cliente_autenticado, eventos):
    """O8 y D4: nunca se guarda `/clausulas?posicion=Defensa`, solo `/clausulas`."""
    from unittest.mock import patch

    vacio = ([], {"managers": [], "posiciones": [], "equipos": []})
    with patch("fantasy.api.app.obtener_clausulas", return_value=vacio):
        cliente_autenticado.get("/clausulas?posicion=Defensa&manager=pepe")

    fila = eventos()[0]
    assert fila.ruta == "/clausulas", "la URL cruda no debe llegar nunca a la tabla"
    assert fila.duracion_ms >= 0


def test_la_escritura_viaja_en_background_y_no_se_ha_ejecutado_al_devolver_la_respuesta():
    """O7 (estructural, ver tasks.md §Descubrimientos de T2).

    `TestClient` espera a las background tasks, así que medir tiempos aquí daría rojo
    aunque el diseño sea correcto. Lo que se garantiza es que, en el instante en que el
    middleware devuelve la respuesta, el `INSERT` todavía NO ha ocurrido y está colgado de
    `response.background`.
    """
    import asyncio
    from unittest.mock import patch

    from fastapi.responses import PlainTextResponse
    from starlette.requests import Request as StarletteRequest

    from fantasy.observabilidad import middleware

    escrituras: list[str] = []

    async def call_next(_request):
        return PlainTextResponse("ok")

    peticion = StarletteRequest(
        {"type": "http", "method": "GET", "path": "/token", "query_string": b"", "headers": []}
    )

    with patch.object(middleware, "registrar_evento", lambda **kw: escrituras.append(kw["ruta"])):
        respuesta = asyncio.run(middleware.registrar_uso(peticion, call_next))

        assert escrituras == [], "el registro se ejecutó ANTES de devolver la respuesta"
        assert respuesta.background is not None

        asyncio.run(respuesta.background())
        assert escrituras == ["(desconocida)"]  # sin ruta enrutada en un scope de juguete


def test_una_ruta_inexistente_no_revienta_el_registro(cliente, eventos):
    """Un 404 no tiene plantilla de ruta asociada: se guarda como desconocida."""
    assert cliente.get(f"/no-existe-{uuid.uuid4().hex[:6]}").status_code == 404

    filas = eventos()
    assert len(filas) == 1
    assert filas[0].ruta == registro.RUTA_DESCONOCIDA
    assert filas[0].estado_http == 404


# --------------------------------------------------------------------------------------
# T7: retención
# --------------------------------------------------------------------------------------


def test_la_purga_borra_los_eventos_viejos_y_respeta_los_recientes(sesion_db, eventos):
    """O21: un evento de hace 200 días desaparece; el de ayer sigue."""
    from datetime import datetime, timedelta, timezone

    from fantasy.storage.retencion import purgar_eventos_uso

    ahora = datetime.now(timezone.utc)
    for hace_dias, ruta in ((200, "/viejo"), (1, "/reciente")):
        sesion_db.add(EventoUso(
            ruta=ruta, tipo=TipoEvento.NAVEGACION, duracion_ms=1, estado_http=200,
            creado_en=ahora - timedelta(days=hace_dias),
        ))
    sesion_db.commit()

    borrados = purgar_eventos_uso(sesion_db, ahora=ahora, retencion_dias=90)

    assert borrados == 1
    assert [e.ruta for e in eventos()] == ["/reciente"]
