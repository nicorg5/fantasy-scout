"""Rutas de auth: login/logout, cuenta de LaLiga, invitaciones y alta (specs/registro)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy.auth import alta, invitaciones
from fantasy.auth.admin import administrador
from fantasy.auth.dependencias import usuario_actual
from fantasy.auth.passwords import normalizar_email, verificar_password
from fantasy.auth.sesiones import NOMBRE_COOKIE, crear_valor_cookie
from fantasy.auth import laliga_login
from fantasy.auth.credenciales_store import (
    borrar_credenciales,
    credenciales_rechazadas,
    guardar_credenciales,
    obtener_token_valido,
    resincronizar_password,
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
    usuario = sesion.scalar(select(Usuario).where(Usuario.email == normalizar_email(email)))

    # El camino normal no toca LaLiga: si está caída, quien pone su contraseña de siempre
    # entra igual. Solo si no casa se prueba contra LaLiga, por si la cambió allí (A16).
    if usuario is None or not (
        verificar_password(password, usuario.password_hash)
        or resincronizar_password(sesion, usuario, password)
    ):
        return _templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"titulo": "Entrar", "error": "Email o contraseña incorrectos."},
            status_code=401,
        )

    return _entrar(usuario.id)


def _entrar(usuario_id: uuid.UUID) -> RedirectResponse:
    """Abre la sesión y lleva a la plantilla. Mismo camino para el login y el alta."""
    respuesta = RedirectResponse(url="/plantilla", status_code=302)
    respuesta.set_cookie(
        key=NOMBRE_COOKIE,
        value=crear_valor_cookie(usuario_id),
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
        "titulo": "Cuenta",
        "guardado": False,
        "error": None,
        "tiene_credenciales": tiene_credenciales(sesion, usuario.id),
        # LaLiga rechaza la contraseña guardada: se pide la nueva en vez de decir
        # "conectada" (specs/registro, A19).
        "rechazadas": credenciales_rechazadas(sesion, usuario.id),
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


# --------------------------------------------------------------------------------------
# Invitaciones (solo admin; specs/registro, bloque A)


def url_publica(request: Request, ruta: str) -> str:
    """URL absoluta para compartir fuera de la app (D8).

    Fuera de local se fuerza `https`: detrás del proxy de Render uvicorn no se fía de
    `X-Forwarded-Proto` y vería `http`, y un enlace `http` a una app con cookie `Secure`
    haría fallar el alta.
    """
    base = request.base_url
    if not obtener_config().es_local:
        base = base.replace(scheme="https")
    return str(base).rstrip("/") + ruta


def _pagina_invitaciones(request: Request, sesion: Session, enlace_nuevo: str | None = None):
    assert _templates is not None
    filas = [(i, invitaciones.estado(i)) for i in invitaciones.listar(sesion)]
    return _templates.TemplateResponse(
        request=request,
        name="invitaciones.html",
        context={"titulo": "Invitación", "filas": filas, "enlace_nuevo": enlace_nuevo},
    )


@router.get("/invitaciones")
def pantalla_invitaciones(
    request: Request,
    admin: Usuario = Depends(administrador),
    sesion: Session = Depends(obtener_sesion),
):
    return _pagina_invitaciones(request, sesion)


@router.post("/invitaciones")
def crear_invitacion(
    request: Request,
    admin: Usuario = Depends(administrador),
    sesion: Session = Depends(obtener_sesion),
):
    """Responde con la página, no con un 302: el enlace solo existe en esta respuesta
    (en la BD queda su hash), así que una redirección lo perdería."""
    _, token = invitaciones.crear(sesion)
    return _pagina_invitaciones(
        request, sesion, enlace_nuevo=url_publica(request, f"/registro?invitacion={token}")
    )


@router.post("/invitaciones/{invitacion_id}/revocar")
def revocar_invitacion(
    invitacion_id: uuid.UUID,
    admin: Usuario = Depends(administrador),
    sesion: Session = Depends(obtener_sesion),
):
    invitaciones.revocar(sesion, invitacion_id)
    return RedirectResponse(url="/invitaciones", status_code=302)


# --------------------------------------------------------------------------------------
# Alta por invitación (specs/registro, bloque B)

# Un único mensaje para inexistente, caducada, usada o revocada (A8): distinguirlos no
# ayuda al invitado y le contaría a un curioso qué enlaces existen.
ENLACE_NO_VALIDO = "Este enlace ha caducado o ya se ha usado. Pide otro a quien te lo mandó."
LALIGA_RECHAZA = (
    "LaLiga no reconoce ese email y contraseña. Si entras en LaLiga Fantasy con Google o "
    "Apple, crea una contraseña desde «He olvidado mi contraseña» en laligafantasy.com y "
    "vuelve a este mismo enlace. Si aun así no funciona, escribe a quien te invitó."
)
LALIGA_NO_RESPONDE = "No hemos podido contactar con LaLiga. Inténtalo de nuevo en un rato."


def _pagina_registro(request: Request, token: str | None, *, valida: bool, codigo: int = 200, **extra):
    assert _templates is not None
    contexto = {
        "titulo": "Crear tu cuenta",
        "valida": valida,
        "invitacion": token if valida else None,
        "email": "",
        "error": None,
        "ya_tiene_cuenta": False,
        "mensaje_no_valida": ENLACE_NO_VALIDO,
    }
    contexto.update(extra)
    return _templates.TemplateResponse(
        request=request, name="registro.html", context=contexto, status_code=codigo
    )


@router.get("/registro")
def formulario_registro(
    request: Request,
    invitacion: str | None = None,
    sesion: Session = Depends(obtener_sesion),
):
    valida = invitaciones.buscar_pendiente(sesion, invitacion) is not None
    return _pagina_registro(request, invitacion, valida=valida)


@router.post("/registro")
def procesar_registro(
    request: Request,
    invitacion: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        usuario = alta.dar_de_alta(sesion, invitacion, email, password)
    except alta.EnlaceNoValido:
        return _pagina_registro(request, invitacion, valida=False, codigo=400)
    except alta.YaTieneCuenta:
        return _pagina_registro(request, invitacion, valida=True, codigo=409, email=email, ya_tiene_cuenta=True)
    except laliga_login.CredencialesInvalidas:
        return _pagina_registro(request, invitacion, valida=True, codigo=400, email=email, error=LALIGA_RECHAZA)
    except laliga_login.LoginNoDisponible:
        return _pagina_registro(request, invitacion, valida=True, codigo=503, email=email, error=LALIGA_NO_RESPONDE)
    return _entrar(usuario.id)
