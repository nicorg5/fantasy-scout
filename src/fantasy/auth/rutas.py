"""Rutas de auth: login/logout, cuenta de LaLiga, invitaciones y alta (specs/registro)."""

from __future__ import annotations

import logging
import urllib.parse
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy.auth import alta, intentos, invitaciones, reconexion
from fantasy.auth.admin import administrador
from fantasy.auth.dependencias import usuario_actual
from fantasy.auth.passwords import normalizar_email, verificar_password
from fantasy.auth.sesiones import NOMBRE_COOKIE, crear_valor_cookie
from fantasy.auth import laliga_login
from fantasy.auth.credenciales_store import (
    EstadoConexion,
    borrar_credenciales,
    buscar_credenciales,
    es_social,
    estado_conexion,
    guardar_credenciales,
    obtener_token_valido,
    resincronizar_password,
    tiene_credenciales,
)
from fantasy.auth.token_store import guardar_token
from fantasy.config import obtener_config
from fantasy.storage.engine import obtener_sesion
from fantasy.storage.modelos import CredencialesLaLiga, Usuario

logger = logging.getLogger("fantasy.auth.rutas")

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
        "rechazadas": estado_conexion(sesion, usuario.id) is EstadoConexion.CONTRASENA_CAMBIADA,
        # Cuenta social (specs/registro-social, S10): sin formulario de contraseña.
        "social": es_social(buscar_credenciales(sesion, usuario.id)),
        "reconectar": estado_conexion(sesion, usuario.id) is EstadoConexion.RECONECTAR,
        "url_login": None,
        "mostrar_pasos": False,
        **contexto_navegador(request),
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
    # Vía con la que se dio de alta cada cuenta (specs/registro-social, S14).
    tipos = dict(sesion.execute(select(CredencialesLaLiga.user_id, CredencialesLaLiga.tipo)).all())
    return _templates.TemplateResponse(
        request=request,
        name="invitaciones.html",
        context={"titulo": "Invitación", "filas": filas, "enlace_nuevo": enlace_nuevo, "tipos": tipos},
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
    "LaLiga no reconoce ese email y contraseña. Si entras en LaLiga Fantasy con Google, "
    "Apple u otra red social, usa la otra opción de esta página."
)
LALIGA_NO_RESPONDE = "No hemos podido contactar con LaLiga. Inténtalo de nuevo en un rato."


def _pagina_registro(request: Request, token: str | None, *, valida: bool, codigo: int = 200, **extra):
    assert _templates is not None
    contexto = {
        "titulo": "Crear tu cuenta",
        "via": "password",
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


# --------------------------------------------------------------------------------------
# Alta con Google u otra red social (specs/registro-social, D6-D7)

# Navegadores integrados en apps: Google no deja iniciar sesión dentro de ellos.
_NAVEGADORES_INTEGRADOS = ("WhatsApp", "Instagram", "FBAN", "FBAV", "FB_IAB")
_MOVILES = ("iPhone", "iPad", "Android", "Mobile")

SOCIAL_DIRECCION_NO_VALIDA = (
    "Eso no parece la dirección de la página de Microsoft. Copia la dirección completa de la "
    "barra, la que empieza por https://jwt.ms/"
)
SOCIAL_LALIGA_NO_COMPLETA = "LaLiga no ha completado el inicio de sesión. Vuelve al paso 1 e inténtalo otra vez."
SOCIAL_INTENTO_NO_VALIDO = "Este intento ha caducado o ya se ha usado. Vuelve al paso 1."
SOCIAL_OTRA_INVITACION = (
    "Esa dirección es de otro enlace de invitación. Usa siempre el mismo enlace con el que "
    "empezaste, o vuelve al paso 1 desde esta página."
)
SOCIAL_OTRA_CUENTA = (
    "Has entrado con otra cuenta de LaLiga, no con la que tenías conectada. Vuelve al paso 1 "
    "y elige la cuenta de siempre."
)
SOCIAL_SIN_EMAIL = "Tu cuenta de LaLiga no nos ha dado un email, así que no podemos crearla. Escribe a quien te invitó."


def contexto_navegador(request: Request) -> dict[str, bool]:
    """Solo para ajustar textos (S18): nada depende de un navegador concreto. La detección
    por `User-Agent` es aproximada, así que el aviso de navegador integrado nunca bloquea."""
    agente = request.headers.get("user-agent", "")
    return {
        "navegador_integrado": any(m in agente for m in _NAVEGADORES_INTEGRADOS),
        "es_movil": any(m in agente for m in _MOVILES),
    }


def _url_social(token: str | None) -> str:
    """Dirección propia de la vía social, con la invitación en la URL. Así Safari puede
    recargarla (lo hace al volver a una pestaña descartada) sin acabar en un 405 y perder
    el formulario (descubierto en la primera alta real, specs/registro-social T12)."""
    return "/registro/social?" + urllib.parse.urlencode({"invitacion": token or ""})


def _pagina_social(request: Request, sesion: Session, token: str, *, codigo: int = 200, **extra):
    """Los tres pasos, siempre con un enlace vigente: el intento de esta invitación si sigue
    vivo, o uno nuevo. Lo pegado **nunca** se devuelve: lleva el código de LaLiga (S7)."""
    fila = invitaciones.buscar_pendiente(sesion, token)
    if fila is None:
        return _pagina_registro(request, token, valida=False, codigo=400)
    _, url_login = intentos.obtener_o_crear(sesion, invitacion_id=fila.id)
    return _pagina_registro(
        request, token, valida=True, codigo=codigo, via="social", url_login=url_login,
        url_social=_url_social(token), **contexto_navegador(request), **extra,
    )


@router.get("/registro/social")
def pantalla_registro_social(
    request: Request,
    invitacion: str | None = None,
    sesion: Session = Depends(obtener_sesion),
):
    """GET a propósito: recargar, volver atrás o reabrir la pestaña deja al usuario en el
    mismo punto, con el mismo intento (que se reutiliza mientras esté vigente)."""
    return _pagina_social(request, sesion, invitacion or "")


@router.api_route("/registro/social/iniciar", methods=["GET", "POST"])
async def iniciar_registro_social(request: Request):
    """Compatibilidad con páginas ya abiertas de la versión anterior (formulario POST, o su
    recarga por GET): todo lleva a la dirección propia de la vía social."""
    formulario = await request.form() if request.method == "POST" else {}
    token = formulario.get("invitacion") or request.query_params.get("invitacion") or ""
    return RedirectResponse(url=_url_social(str(token)), status_code=303)


@router.post("/registro/social")
def procesar_registro_social(
    request: Request,
    invitacion: str = Form(""),
    pegado: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    sesion: Session = Depends(obtener_sesion),
):
    invitacion = invitacion or request.query_params.get("invitacion", "")
    try:
        usuario = alta.dar_de_alta_social(sesion, invitacion, pegado, password, password2)
    except alta.EnlaceNoValido:
        _anotar_rechazo_social("alta", "EnlaceNoValido")
        return _pagina_registro(request, invitacion, valida=False, codigo=400)
    except alta.YaTieneCuenta:
        _anotar_rechazo_social("alta", "YaTieneCuenta")
        return _pagina_social(request, sesion, invitacion, codigo=409, ya_tiene_cuenta=True)
    except (alta.ContrasenaNoValida, alta.DireccionNoValida, laliga_login.ErrorEnLaDireccion,
            alta.IntentoNoValido, alta.SinEmail, laliga_login.LoginNoDisponible) as exc:
        error, codigo = _mensaje_error_social(exc)
        _anotar_rechazo_social("alta", type(exc).__name__)
        return _pagina_social(request, sesion, invitacion, codigo=codigo, error=error)
    return _entrar(usuario.id)


def _anotar_rechazo_social(via: str, motivo: str) -> None:
    """Deja en el log de Render **por qué** se rechazó un intento: solo el tipo de error,
    nunca la dirección pegada, el código ni la contraseña. Sin esto, la primera alta real
    fallida no dejó rastro de su motivo (specs/registro-social T12)."""
    logger.info("%s social rechazada: %s", via, motivo)


def _mensaje_error_social(exc: Exception) -> tuple[str, int]:
    """Cada error de la vía social, en lenguaje llano y con su código HTTP."""
    if isinstance(exc, alta.ContrasenaNoValida):
        error, codigo = str(exc), 400
    elif isinstance(exc, alta.DireccionNoValida):
        error, codigo = SOCIAL_DIRECCION_NO_VALIDA, 400
    elif isinstance(exc, laliga_login.ErrorEnLaDireccion):
        logger.info("LaLiga devolvió un error en la dirección social: %s", exc)  # sin código ni credenciales
        error, codigo = SOCIAL_LALIGA_NO_COMPLETA, 400
    elif isinstance(exc, alta.IntentoDeOtraInvitacion):
        error, codigo = SOCIAL_OTRA_INVITACION, 400
    elif isinstance(exc, alta.IntentoNoValido):
        error, codigo = SOCIAL_INTENTO_NO_VALIDO, 400
    elif isinstance(exc, alta.SinEmail):
        error, codigo = SOCIAL_SIN_EMAIL, 400
    elif isinstance(exc, reconexion.OtraCuenta):
        error, codigo = SOCIAL_OTRA_CUENTA, 400
    else:
        error, codigo = LALIGA_NO_RESPONDE, 503
    return error, codigo


# --------------------------------------------------------------------------------------
# Reconexión de una cuenta social desde Cuenta (specs/registro-social, S10-S11)


def _pagina_reconexion(request: Request, sesion: Session, usuario: Usuario, **extra):
    _, url_login = intentos.obtener_o_crear(sesion, usuario_id=usuario.id)
    return _pagina_token(request, sesion, usuario, url_login=url_login, mostrar_pasos=True, **extra)


@router.get("/token/social")
def pantalla_reconexion_social(
    request: Request,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    """GET por lo mismo que `/registro/social`: que recargar no rompa nada."""
    if not es_social(buscar_credenciales(sesion, usuario.id)):
        return RedirectResponse(url="/token", status_code=302)
    return _pagina_reconexion(request, sesion, usuario)


@router.api_route("/token/social/iniciar", methods=["GET", "POST"])
def iniciar_reconexion_social():
    """Compatibilidad con páginas de la versión anterior."""
    return RedirectResponse(url="/token/social", status_code=303)


@router.post("/token/social")
def procesar_reconexion_social(
    request: Request,
    pegado: str = Form(""),
    usuario: Usuario = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        reconexion.reconectar_social(sesion, usuario, pegado)
    except reconexion.NoEsSocial:
        return RedirectResponse(url="/token", status_code=302)
    except (alta.DireccionNoValida, laliga_login.ErrorEnLaDireccion, alta.IntentoNoValido,
            reconexion.OtraCuenta, laliga_login.LoginNoDisponible) as exc:
        error, codigo = _mensaje_error_social(exc)
        _anotar_rechazo_social("reconexión", type(exc).__name__)
        return _pagina_reconexion(request, sesion, usuario, error=error, _codigo=codigo)
    return RedirectResponse(url="/plantilla", status_code=302)
