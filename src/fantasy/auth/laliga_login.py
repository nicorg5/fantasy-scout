"""Login programático contra LaLiga Fantasy (Azure B2C).

Dos flujos:

- **ROPC** (email y contraseña): descubierto en el bundle de `Externoak/LaLigaApp` v3.5.3 y
  verificado contra el endpoint real (ver design.md §Login automático).
- **Social** (Google, Apple…): código de autorización + PKCE con vuelta a `https://jwt.ms`,
  la única dirección de vuelta registrada que deja el código visible en un iPhone. Verificado
  con una cuenta real de Google (specs/registro-social, design.md D3).

**Aquí se manejan credenciales en claro en memoria.** Nada de lo que pasa por este módulo
puede acabar en un log, en una excepción con detalle, ni en una respuesta HTTP.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("fantasy.auth.laliga_login")

BASE_URL = "https://login.laliga.es/laligadspprob2c.onmicrosoft.com/oauth2/v2.0/token"
POLICY_LOGIN = "B2C_1A_ResourceOwnerv2"
POLICY_REFRESH = "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"
REDIRECT_URI = "authredirect://com.lfp.laligafantasy"
TIMEOUT_SEGUNDOS = 20.0

# Flujo social (specs/registro-social, D3). Misma política que la renovación.
AUTHORIZE_URL = BASE_URL.removesuffix("/token") + "/authorize"
POLICY_SOCIAL = POLICY_REFRESH
REDIRECT_SOCIAL = "https://jwt.ms"
# El verificado: un token emitido con este scope se renueva con este mismo scope.
SCOPE_SOCIAL = "openid offline_access"
# B2C responde con este código cuando la dirección de vuelta no está registrada.
ERROR_VUELTA_NO_REGISTRADA = "AADB2C90006"

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


def iniciar_sesion(email: str, password: str) -> TokensLaLiga:
    """Obtiene tokens con email y contraseña. Las credenciales solo viven en memoria."""
    payload = _pedir(
        f"{BASE_URL}?p={POLICY_LOGIN}",
        {
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "scope": f"openid {CLIENT_ID} offline_access",
            "redirect_uri": REDIRECT_URI,
            "username": email,
            "password": password,
            "response_type": "id_token",
        },
    )
    logger.info("login contra LaLiga correcto")  # sin email: es un dato personal
    return _tokens_desde(payload)


def refrescar(refresh_token: str) -> TokensLaLiga:
    """Renueva sin credenciales. Se intenta antes que un login completo."""
    payload = _pedir(
        f"{BASE_URL}?p={POLICY_REFRESH}",
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "scope": f"openid {CLIENT_ID} offline_access",
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
    logger.info("canje de código social correcto (idp %s)", claims.get("idp", "?"))
    return TokensSocial(tokens=tokens, email=email, oid=claims.get("oid") or claims.get("sub"))


def refrescar_social(refresh_token: str) -> TokensLaLiga:
    """Como `refrescar`, con el scope con el que se emitió el token social."""
    payload = _pedir(
        f"{BASE_URL}?p={POLICY_SOCIAL}",
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "scope": SCOPE_SOCIAL,
            "refresh_token": refresh_token,
        },
    )
    logger.info("refresh de token social correcto")
    return _tokens_desde(payload)


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
