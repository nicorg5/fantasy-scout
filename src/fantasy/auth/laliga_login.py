"""Login programático contra LaLiga Fantasy (Azure B2C, flujo ROPC).

Descubierto en el bundle de `Externoak/LaLigaApp` v3.5.3 y verificado contra el endpoint
real (ver design.md §Login automático). Es el mismo flujo que usa la app oficial.

**Aquí se manejan credenciales en claro en memoria.** Nada de lo que pasa por este módulo
puede acabar en un log, en una excepción con detalle, ni en una respuesta HTTP.
"""

from __future__ import annotations

import logging
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
