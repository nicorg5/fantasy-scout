"""App FastAPI: sirve la API y el HTML en el mismo proceso (ver design.md §Stack)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from fantasy.analytics.presentacion import calcular_variacion, formatear_euros
from fantasy.analytics.clausulas import FiltrosClausulas, obtener_clausulas
from fantasy.analytics.servicio import (
    fecha_de_la_analitica,
    obtener_mercado,
    obtener_plantilla,
)
from fantasy.auth.dependencias import NoAutenticado, usuario_actual
from fantasy.auth.rutas import montar_templates
from fantasy.auth.rutas import router as auth_router
from fantasy.official.errores import ErrorAPIOficial, TokenInvalido
from fantasy.storage.engine import obtener_sesion
from fantasy.storage.modelos import Usuario

logger = logging.getLogger("fantasy.api")

DIRECTORIO_API = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(DIRECTORIO_API / "templates"))
TEMPLATES.env.filters["euros"] = formatear_euros
montar_templates(TEMPLATES)

app = FastAPI(title="fantasy-scout", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(DIRECTORIO_API / "static")), name="static")
app.include_router(auth_router)


@app.exception_handler(NoAutenticado)
def _redirigir_a_login(request: Request, exc: NoAutenticado) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def raiz() -> RedirectResponse:
    return RedirectResponse(url="/plantilla")


def _pagina_de_jugadores(request, plantilla_html: str, titulo: str, cargar, sesion=None):
    """Renderiza una pantalla de jugadores traduciendo los fallos esperados en avisos.

    Un token caducado es un evento normal (dura 24h), no un error del servidor: se
    responde 200 con un aviso y enlace a /token, nunca un 500 (R13).
    """
    try:
        jugadores = cargar()
    except TokenInvalido:
        return TEMPLATES.TemplateResponse(
            request=request,
            name=plantilla_html,
            context={
                "titulo": titulo,
                "jugadores": [],
                "aviso_token": "Tu token de LaLiga ha caducado o no es válido. Vuelve a pegarlo para ver tus datos.",
            },
        )
    except ErrorAPIOficial as exc:
        logger.warning("fallo de la API oficial en %s: %s", titulo, exc)
        return TEMPLATES.TemplateResponse(
            request=request,
            name=plantilla_html,
            context={
                "titulo": titulo,
                "jugadores": [],
                "aviso_error": "No se ha podido contactar con LaLiga Fantasy ahora mismo. Vuelve a intentarlo en un rato.",
            },
        )

    return TEMPLATES.TemplateResponse(
        request=request,
        name=plantilla_html,
        context={
            "titulo": titulo,
            "jugadores": jugadores,
            "analitica_de": fecha_de_la_analitica(sesion),
            # Solo tiene sentido en la plantilla propia: el mercado no es "tu" equipo.
            "variacion": calcular_variacion(jugadores) if plantilla_html == "plantilla.html" else None,
        },
    )


def _fragmento_tabla(request, titulo: str, cargar, con_variacion: bool = False):
    """Devuelve SOLO la tabla, no la página entera: es lo que hace útil a htmx (R38).

    Un fallo aquí no puede devolver la página completa dentro del hueco de la tabla, así
    que los errores se renderizan como una fila de aviso dentro del mismo fragmento.
    """
    try:
        jugadores = cargar()
    except TokenInvalido:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="_tabla_error.html",
            context={"mensaje": "Tu sesión con LaLiga ha caducado. Recarga la página."},
        )
    except ErrorAPIOficial as exc:
        logger.warning("fallo de la API oficial en el fragmento %s: %s", titulo, exc)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="_tabla_error.html",
            context={"mensaje": "No se ha podido contactar con LaLiga Fantasy."},
        )

    # /plantilla devuelve variacion + tabla en un contenedor comun, para que el swap de
    # htmx sustituya el bloque entero y el total no quede desfasado ni duplicado.
    # /mercado solo devuelve la tabla: ahi la variacion no significa nada.
    return TEMPLATES.TemplateResponse(
        request=request,
        name="_datos_plantilla.html" if con_variacion else "_tabla_jugadores.html",
        context={
            "jugadores": jugadores,
            "variacion": calcular_variacion(jugadores) if con_variacion else None,
        },
    )


@app.get("/clausulas")
def clausulas(
    request: Request,
    manager: str | None = None,
    posicion: str | None = None,
    equipo: str | None = None,
    orden: str | None = None,
    estado: str | None = None,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    """Clausulazos de la liga.

    Mas lenta que el resto (~5 s): lee las plantillas de los 9 managers rivales en vivo.
    No se cachea a proposito: una clausula desactualizada llevaria a intentar un fichaje
    imposible.
    """
    filtros = FiltrosClausulas.desde_query(manager, posicion, equipo, orden, estado)
    contexto = {
        "titulo": "Clausulazos",
        "filtros": filtros,
        "filtros_activos": any(
            (filtros.manager, filtros.posicion, filtros.equipo, filtros.estado)
        ),
        "opciones": {"managers": [], "posiciones": [], "equipos": []},
        "clausulas": [],
        "analitica_de": None,
    }

    try:
        filas, opciones = obtener_clausulas(sesion, usuario.id, filtros)
    except TokenInvalido:
        contexto["aviso_token"] = (
            "Tu token de LaLiga ha caducado o no es válido. Vuelve a pegarlo para ver tus datos."
        )
        return TEMPLATES.TemplateResponse(request=request, name="clausulas.html", context=contexto)
    except ErrorAPIOficial as exc:
        logger.warning("fallo de la API oficial en clausulas: %s", exc)
        contexto["aviso_error"] = (
            "No se ha podido contactar con LaLiga Fantasy ahora mismo. Vuelve a intentarlo en un rato."
        )
        return TEMPLATES.TemplateResponse(request=request, name="clausulas.html", context=contexto)

    contexto["clausulas"] = filas
    contexto["opciones"] = opciones
    contexto["analitica_de"] = fecha_de_la_analitica(sesion)
    return TEMPLATES.TemplateResponse(request=request, name="clausulas.html", context=contexto)


@app.get("/plantilla/tabla")
def plantilla_tabla(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    return _fragmento_tabla(
        request, "Mi plantilla", lambda: obtener_plantilla(sesion, usuario.id),
        con_variacion=True,
    )


@app.get("/mercado/tabla")
def mercado_tabla(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    return _fragmento_tabla(request, "Mercado de hoy", lambda: obtener_mercado(sesion, usuario.id))


@app.get("/plantilla")
def plantilla(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    return _pagina_de_jugadores(
        request, "plantilla.html", "Mi plantilla",
        lambda: obtener_plantilla(sesion, usuario.id), sesion,
    )


@app.get("/mercado")
def mercado(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    return _pagina_de_jugadores(
        request, "mercado.html", "Mercado de hoy",
        lambda: obtener_mercado(sesion, usuario.id), sesion,
    )
