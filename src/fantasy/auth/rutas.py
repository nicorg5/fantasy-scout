"""Rutas de login/logout. No hay /registro: el alta es manual (scripts/crear_usuario.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy.auth.dependencias import usuario_actual
from fantasy.auth.passwords import verificar_password
from fantasy.auth.sesiones import NOMBRE_COOKIE, crear_valor_cookie
from fantasy.auth import laliga_login
from fantasy.auth.credenciales_store import (
    borrar_credenciales,
    guardar_credenciales,
    obtener_token_valido,
    tiene_credenciales,
)
from fantasy.auth.token_store import guardar_token
from fantasy.config import obtener_config
from fantasy.storage.engine import obtener_sesion
from fantasy.storage.modelos import Usuario

router = APIRouter()

_templates: Jinja2Templates | None = None


def montar_templates(templates: Jinja2Templates) -> None:
    global _templates
    _templates = templates


def _cookie_segura() -> bool:
    # En local (HTTP) Secure impediría que el navegador guarde la cookie; en producción
    # (Render, siempre HTTPS) debe ir activa. Ver R7 en requirements.md.
    return not obtener_config().es_local


@router.get("/login")
def formulario_login(request: Request):
    assert _templates is not None
    return _templates.TemplateResponse(
        request=request, name="login.html", context={"titulo": "Entrar", "error": None}
    )


@router.post("/login")
def procesar_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    sesion: Session = Depends(obtener_sesion),
):
    assert _templates is not None
    usuario = sesion.scalar(select(Usuario).where(Usuario.email == email))

    if usuario is None or not verificar_password(password, usuario.password_hash):
        return _templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"titulo": "Entrar", "error": "Email o contraseña incorrectos."},
            status_code=401,
        )

    respuesta = RedirectResponse(url="/plantilla", status_code=302)
    respuesta.set_cookie(
        key=NOMBRE_COOKIE,
        value=crear_valor_cookie(usuario.id),
        httponly=True,
        secure=_cookie_segura(),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return respuesta


@router.post("/logout")
def logout():
    respuesta = RedirectResponse(url="/login", status_code=302)
    respuesta.delete_cookie(NOMBRE_COOKIE)
    return respuesta


def _pagina_token(request, sesion, usuario, **extra):
    assert _templates is not None
    contexto = {
        "titulo": "Tu token",
        "guardado": False,
        "error": None,
        "tiene_credenciales": tiene_credenciales(sesion, usuario.id),
    }
    contexto.update(extra)
    codigo = contexto.pop("_codigo", 200)
    return _templates.TemplateResponse(
        request=request, name="token.html", context=contexto, status_code=codigo
    )


@router.get("/token")
def formulario_token(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    return _pagina_token(request, sesion, usuario)


@router.post("/credenciales")
def conectar_cuenta(
    request: Request,
    email_laliga: str = Form(...),
    password_laliga: str = Form(...),
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    """Conecta la cuenta de LaLiga para que el token se renueve solo.

    Las credenciales se validan ANTES de guardarlas: no tiene sentido persistir algo que
    no funciona, y el usuario se entera al momento.
    """
    try:
        laliga_login.iniciar_sesion(email_laliga, password_laliga)
    except laliga_login.CredencialesInvalidas:
        return _pagina_token(
            request, sesion, usuario,
            error="LaLiga ha rechazado ese email o esa contraseña. No se ha guardado nada.",
            _codigo=400,
        )
    except laliga_login.ErrorLoginLaLiga:
        return _pagina_token(
            request, sesion, usuario,
            error="No se ha podido contactar con el login de LaLiga. Inténtalo más tarde.",
            _codigo=503,
        )

    guardar_credenciales(sesion, usuario.id, email_laliga, password_laliga)
    obtener_token_valido(sesion, usuario.id)
    return RedirectResponse(url="/plantilla", status_code=302)


@router.post("/credenciales/borrar")
def desconectar_cuenta(
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    borrar_credenciales(sesion, usuario.id)
    return RedirectResponse(url="/token", status_code=302)


@router.post("/token")
def procesar_token(
    request: Request,
    token: str = Form(...),
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    assert _templates is not None
    token = token.strip()
    if not token:
        return _pagina_token(
            request, sesion, usuario, error="El token no puede estar vacío.", _codigo=400
        )

    guardar_token(sesion, usuario.id, token)
    return _pagina_token(request, sesion, usuario, guardado=True)
