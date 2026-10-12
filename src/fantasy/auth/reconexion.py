"""Reconexión de una cuenta social desde Cuenta (specs/registro-social, D5, S10-S11).

Mismo flujo que el alta social, pero el intento es del usuario y no de una invitación, y
solo se acepta **la misma cuenta de LaLiga**: si el `oid` del token no es el guardado, se
rechaza sin escribir nada. Así nadie puede colgar otra cuenta de LaLiga en la suya.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fantasy.auth import intentos, laliga_login
from fantasy.auth.alta import DireccionNoValida, IntentoNoValido
from fantasy.auth.credenciales_store import buscar_credenciales, es_social, escribir_tokens
from fantasy.storage.modelos import Usuario


class OtraCuenta(Exception):
    """La cuenta de LaLiga con la que ha entrado no es la que tenía conectada."""


class NoEsSocial(Exception):
    """El usuario no tiene una cuenta social: no hay nada que reconectar por aquí."""


def reconectar_social(sesion: Session, usuario: Usuario, pegado: str) -> None:
    """Lanza `NoEsSocial`, `DireccionNoValida`, `laliga_login.ErrorEnLaDireccion`,
    `IntentoNoValido`, `laliga_login.LoginNoDisponible` u `OtraCuenta`. Si no lanza, la
    cuenta queda conectada otra vez (token y refresh nuevos, sin error)."""
    credenciales = buscar_credenciales(sesion, usuario.id)
    if not es_social(credenciales):
        raise NoEsSocial()
    try:
        state, code = laliga_login.extraer_codigo(pegado)
    except laliga_login.DireccionSinCodigo:
        raise DireccionNoValida() from None

    try:
        intento = intentos.buscar_vigente(sesion, state)
        if intento is None or intento.usuario_id != usuario.id:
            raise IntentoNoValido()
        verificador = intentos.consumir(sesion, intento)
        try:
            social = laliga_login.canjear_codigo(code, verificador)
        except laliga_login.CredencialesInvalidas:
            raise IntentoNoValido() from None
        if credenciales.laliga_oid and social.oid != credenciales.laliga_oid:
            raise OtraCuenta()
        credenciales.laliga_oid = credenciales.laliga_oid or social.oid
        escribir_tokens(sesion, usuario.id, social.tokens, credenciales)
        sesion.commit()
    except BaseException:
        sesion.rollback()
        raise
