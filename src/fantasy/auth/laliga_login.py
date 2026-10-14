"""Login programático contra LaLiga Fantasy (Azure B2C).

Los dos flujos acaban en el mismo código de autorización + PKCE con vuelta a `https://jwt.ms`,
la única dirección de vuelta registrada que deja el código visible en un iPhone:

- **Email y contraseña**: se recorre sin navegador la página de login de B2C (authorize →
  SelfAsserted → confirmed), rellenando el formulario de cuenta local. Sustituye a ROPC
  (`B2C_1A_ResourceOwnerv2`), que LaLiga retiró el 13/10/2026: su endpoint responde 404.
  Verificado con una cuenta real el 14/10/2026.
- **Social** (Google, Apple…): el usuario hace el login en su navegador y pega la dirección
  de vuelta. Verificado con una cuenta real de Google (specs/registro-social, design.md D3).

**Aquí se manejan credenciales en claro en memoria.** Nada de lo que pasa por este módulo
puede acabar en un log, en una excepción con detalle, ni en una respuesta HTTP.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import json
import logging
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("fantasy.auth.laliga_login")

HOST_LOGIN = "https://login.laliga.es"
BASE_URL = f"{HOST_LOGIN}/laligadspprob2c.onmicrosoft.com/oauth2/v2.0/token"
POLICY_REFRESH = "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"
TIMEOUT_SEGUNDOS = 20.0

# Flujo de código (specs/registro-social, D3). Misma política que la renovación.
AUTHORIZE_URL = BASE_URL.removesuffix("/token") + "/authorize"
POLICY_SOCIAL = POLICY_REFRESH
REDIRECT_SOCIAL = "https://jwt.ms"
# El verificado: un token emitido con este scope se renueva con este mismo scope.
SCOPE_SOCIAL = "openid offline_access"
# B2C responde con este código cuando la dirección de vuelta no está registrada.
ERROR_VUELTA_NO_REGISTRADA = "AADB2C90006"

# Formulario de cuenta local de la página de login (`SA_FIELDS` de la página).
CAMPO_EMAIL = "signInName"
CAMPO_PASSWORD = "password_login"
# Códigos de B2C que significan "email o contraseña mal". Cualquier otro rechazo del
# formulario es un fallo nuestro o de LaLiga, no del usuario: no se le culpa.
ERRORES_CREDENCIALES = {"AADB2C90053", "AADB2C90054"}
UA_NAVEGADOR = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"

# Vigencia de respaldo si la respuesta no dice cuánto dura (el JWT real dura 24 h).
VIGENCIA_RESPALDO = timedelta(hours=24)


class ErrorLoginLaLiga(Exception):
    """Base de los fallos autenticando contra LaLiga."""


class CredencialesInvalidas(ErrorLoginLaLiga):
    """LaLiga rechazó email o contraseña.

    Se distingue del fallo de red a propósito: reintentar con credenciales malas no
    arregla nada y machaca el login de LaLiga. Quien llama debe pedirlas de nuevo.
    """


class LoginNoDisponible(ErrorLoginLaLiga):
    """No se pudo contactar con el login, o respondió algo inesperado. Reintentar sí tiene
    sentido: puede ser un problema pasajero, o que hayan cerrado el flujo ROPC."""


@dataclass(frozen=True)
class TokensLaLiga:
    access_token: str
    refresh_token: str | None
    expira_en: datetime


def _expiracion(payload: dict) -> datetime:
    """La respuesta puede traer `expires_on` (absoluto) o `expires_in` (relativo)."""
    ahora = datetime.now(timezone.utc)
    if payload.get("expires_on"):
        try:
            return datetime.fromtimestamp(int(payload["expires_on"]), tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    for clave in ("expires_in", "id_token_expires_in"):
        if payload.get(clave):
            try:
                return ahora + timedelta(seconds=int(payload[clave]))
            except (TypeError, ValueError):
                pass
    return ahora + VIGENCIA_RESPALDO


def _tokens_desde(payload: dict) -> TokensLaLiga:
    # La app oficial usa `id_token` como bearer contra la API (ver AUTH_CONFIG del bundle:
    # `e.id_token || e.access_token`), así que se prefiere ese orden.
    token = payload.get("id_token") or payload.get("access_token")
    if not token:
        raise LoginNoDisponible("la respuesta del login no traía ningún token")
    return TokensLaLiga(
        access_token=token,
        refresh_token=payload.get("refresh_token"),
        expira_en=_expiracion(payload),
    )


def _pedir(url: str, datos: dict[str, str]) -> dict:
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as cliente:
            respuesta = cliente.post(
                url,
                data=datos,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        # Sin detalle del cuerpo: podría arrastrar credenciales.
        raise LoginNoDisponible(f"no se pudo contactar con el login de LaLiga: {exc}") from exc

    if respuesta.status_code == 400:
        # 400 en este endpoint es "credenciales mal", no un fallo de servicio.
        raise CredencialesInvalidas("LaLiga rechazó el email o la contraseña.")
    if respuesta.status_code != 200:
        raise LoginNoDisponible(f"el login de LaLiga respondió HTTP {respuesta.status_code}")

    try:
        return respuesta.json()
    except ValueError as exc:
        raise LoginNoDisponible("el login de LaLiga no devolvió JSON") from exc


def refrescar(refresh_token: str) -> TokensLaLiga:
    """Renueva sin credenciales. Se intenta antes que un login completo.

    Con el scope del flujo de código, que es con el que `iniciar_sesion` emite ahora los
    tokens. Un refresh token antiguo de ROPC que LaLiga ya no acepte cae al login completo.
    """
    payload = _pedir(
        f"{BASE_URL}?p={POLICY_REFRESH}",
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "scope": SCOPE_SOCIAL,
            "refresh_token": refresh_token,
        },
    )
    logger.info("refresh de token contra LaLiga correcto")
    return _tokens_desde(payload)


# --------------------------------------------------------------------------------------
# Flujo social: código de autorización + PKCE (specs/registro-social, D3)


class DireccionSinCodigo(Exception):
    """Lo pegado no contiene `code`: no es la dirección de la página de vuelta."""


class ErrorEnLaDireccion(Exception):
    """LaLiga devolvió un `error` en la dirección de vuelta. Lleva su descripción, que la
    ruta traduce a lenguaje llano; no contiene credenciales."""


@dataclass(frozen=True)
class TokensSocial:
    tokens: TokensLaLiga
    email: str | None
    oid: str | None  # identificador del usuario en LaLiga


def reto_pkce(verificador: str) -> str:
    """`code_challenge` S256 del verificador."""
    digest = hashlib.sha256(verificador.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def url_login_social(state: str, verificador: str) -> str:
    """Enlace al login oficial de LaLiga. B2C exige `nonce`; se usa el `state`, que es
    aleatorio y no hay nada más a lo que atarlo."""
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "p": POLICY_SOCIAL,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_SOCIAL,
        "response_type": "code",
        # Tras "#": el navegador nunca envía esta parte a jwt.ms.
        "response_mode": "fragment",
        "scope": SCOPE_SOCIAL,
        "code_challenge": reto_pkce(verificador),
        "code_challenge_method": "S256",
        "state": state,
        "nonce": state,
    })


def extraer_codigo(pegado: str) -> tuple[str | None, str]:
    """(state, code) de lo que pega el usuario: la dirección entera o solo lo de tras `#`.

    Lanza `ErrorEnLaDireccion` si LaLiga devolvió un error, o `DireccionSinCodigo` si no hay
    código.
    """
    texto = pegado.strip()
    for separador in ("#", "?"):
        if separador in texto:
            texto = texto.split(separador, 1)[1]
            break
    params = urllib.parse.parse_qs(texto)
    if "error" in params:
        descripcion = (params.get("error_description") or params["error"])[0]
        raise ErrorEnLaDireccion(descripcion)
    if not params.get("code"):
        raise DireccionSinCodigo()
    return (params.get("state") or [None])[0], params["code"][0]


def _claims(jwt: str) -> dict:
    """Carga del `id_token`, sin validar la firma: llega directamente del endpoint `token`
    de LaLiga por TLS en respuesta a nuestro canje con PKCE (OpenID Connect lo permite)."""
    try:
        carga = jwt.split(".")[1]
        carga += "=" * (-len(carga) % 4)
        return json.loads(base64.urlsafe_b64decode(carga))
    except (IndexError, ValueError):
        return {}


def canjear_codigo(code: str, verificador: str) -> TokensSocial:
    """Canjea el código por tokens. Un 400 (código caducado o ya usado) es
    `CredencialesInvalidas`; un fallo de servicio, `LoginNoDisponible`."""
    payload = _pedir(
        f"{BASE_URL}?p={POLICY_SOCIAL}",
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "code_verifier": verificador,
            "redirect_uri": REDIRECT_SOCIAL,
            "scope": SCOPE_SOCIAL,
        },
    )
    tokens = _tokens_desde(payload)
    claims = _claims(payload.get("id_token") or "")
    emails = claims.get("emails") or []
    email = claims.get("email") or (emails[0] if emails else None)
    logger.info("canje de código correcto (idp %s)", claims.get("idp", "?"))
    return TokensSocial(tokens=tokens, email=email, oid=claims.get("oid") or claims.get("sub"))


def refrescar_social(refresh_token: str) -> TokensLaLiga:
    """Igual que `refrescar`: ambos flujos emiten ya los tokens con el mismo scope. Se
    mantiene aparte porque una cuenta social **solo** se renueva por aquí (D5)."""
    return refrescar(refresh_token)


# --------------------------------------------------------------------------------------
# Email y contraseña: la página de login de B2C recorrida sin navegador


def _ajuste_de_pagina(html: str, clave: str) -> str | None:
    """Valor de texto de `var SETTINGS = {...}` en la página de login de B2C."""
    m = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(clave), html)
    return m.group(1) if m else None


def iniciar_sesion(email: str, password: str) -> TokensLaLiga:
    """Obtiene tokens con email y contraseña. Las credenciales solo viven en memoria.

    Lanza `CredencialesInvalidas` si LaLiga rechaza email o contraseña, y
    `LoginNoDisponible` ante cualquier otra cosa (red, página cambiada, pantalla nueva).
    Ningún mensaje lleva el cuerpo de una respuesta: podría arrastrar las credenciales.
    """
    state = secrets.token_urlsafe(16)
    verificador = secrets.token_urlsafe(48)
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS, follow_redirects=False,
                          headers={"User-Agent": UA_NAVEGADOR}) as cliente:
            # 1. Página de login: abre la transacción (cookies) y trae su CSRF.
            pagina = cliente.get(url_login_social(state, verificador))
            if pagina.status_code != 200:
                raise LoginNoDisponible(f"la página de login de LaLiga respondió HTTP {pagina.status_code}")
            csrf, tx, api, tenant = (
                _ajuste_de_pagina(pagina.text, k) for k in ("csrf", "transId", "api", "tenant")
            )
            if not (csrf and tx and api and tenant):
                raise LoginNoDisponible("la página de login de LaLiga ha cambiado: falta la transacción")
            politica = tenant.rsplit("/", 1)[-1]

            # 2. El formulario de cuenta local.
            envio = cliente.post(
                f"{HOST_LOGIN}{tenant}/SelfAsserted",
                params={"tx": tx, "p": politica},
                data={"request_type": "RESPONSE", CAMPO_EMAIL: email, CAMPO_PASSWORD: password},
                headers={"X-CSRF-TOKEN": csrf, "X-Requested-With": "XMLHttpRequest",
                         "Origin": HOST_LOGIN, "Referer": str(pagina.url)},
            )
            try:
                resultado = envio.json()
            except ValueError:
                raise LoginNoDisponible(f"el login de LaLiga respondió HTTP {envio.status_code} sin JSON") from None
            if resultado.get("status") != "200":
                codigo = resultado.get("errorCode", "?")
                if codigo in ERRORES_CREDENCIALES:
                    raise CredencialesInvalidas("LaLiga rechazó el email o la contraseña.")
                raise LoginNoDisponible(f"el login de LaLiga rechazó el formulario ({codigo})")

            # 3. Confirmación: B2C redirige a la vuelta con el código.
            confirmacion = cliente.get(
                f"{HOST_LOGIN}{tenant}/api/{api}/confirmed",
                params={"rememberMe": "false", "csrf_token": csrf, "tx": tx, "p": politica},
            )
            destino = confirmacion.headers.get("location", "")
    except httpx.HTTPError as exc:
        raise LoginNoDisponible(f"no se pudo contactar con el login de LaLiga: {type(exc).__name__}") from None

    if not destino.startswith(REDIRECT_SOCIAL):
        # p. ej. LaLiga añade una pantalla (condiciones, verificación) tras la contraseña.
        raise LoginNoDisponible(f"el login de LaLiga no devolvió el código (HTTP {confirmacion.status_code})")
    try:
        estado, code = extraer_codigo(destino)
    except (DireccionSinCodigo, ErrorEnLaDireccion):
        raise LoginNoDisponible("el login de LaLiga devolvió un error en lugar del código") from None
    if estado != state:
        raise LoginNoDisponible("el login de LaLiga devolvió un código de otra transacción")

    # 4. Canje. Un 400 aquí es el código, no la contraseña: no se culpa al usuario.
    try:
        tokens = canjear_codigo(code, verificador).tokens
    except CredencialesInvalidas:
        raise LoginNoDisponible("LaLiga rechazó el canje del código") from None
    logger.info("login contra LaLiga correcto")  # sin email: es un dato personal
    return tokens


class ResultadoSondeo(str, enum.Enum):
    ACEPTADA = "aceptada"
    RECHAZADA = "rechazada"
    NO_COMPROBADA = "no_comprobada"


@dataclass(frozen=True)
class SondeoVuelta:
    resultado: ResultadoSondeo
    detalle: str


def _sondear_una_vez() -> SondeoVuelta:
    # Verificador de usar y tirar: el enlace nunca se abre, solo se mira qué responde B2C.
    url = url_login_social("sondeo", "sondeo-" + "x" * 43)
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as cliente:
            respuesta = cliente.get(url, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0"})
    except httpx.HTTPError as exc:
        return SondeoVuelta(ResultadoSondeo.NO_COMPROBADA, f"sin respuesta del login de LaLiga: {type(exc).__name__}")

    contenido = urllib.parse.unquote(respuesta.text + respuesta.headers.get("location", ""))
    if ERROR_VUELTA_NO_REGISTRADA in contenido:
        return SondeoVuelta(ResultadoSondeo.RECHAZADA, f"LaLiga ya no acepta {REDIRECT_SOCIAL} ({ERROR_VUELTA_NO_REGISTRADA})")
    if respuesta.status_code == 200 and "GoogleExchange" in respuesta.text:
        return SondeoVuelta(ResultadoSondeo.ACEPTADA, f"LaLiga acepta {REDIRECT_SOCIAL} y ofrece Google")
    return SondeoVuelta(ResultadoSondeo.NO_COMPROBADA, f"respuesta inesperada del login de LaLiga (HTTP {respuesta.status_code})")


def sondear_vuelta_social(espera_reintento: float = 30.0) -> SondeoVuelta:
    """¿Sigue LaLiga aceptando `https://jwt.ms`? Un GET a su login, sin credenciales
    (specs/registro-social, D10). Si no se puede saber, reintenta una vez."""
    sondeo = _sondear_una_vez()
    if sondeo.resultado is ResultadoSondeo.NO_COMPROBADA:
        time.sleep(espera_reintento)
        sondeo = _sondear_una_vez()
    return sondeo
