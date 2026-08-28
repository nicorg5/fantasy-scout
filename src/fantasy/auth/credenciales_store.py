"""Almacenamiento cifrado de las credenciales de LaLiga y obtención automática del token.

Este módulo es el que hace que la app funcione sin intervención diaria (y por tanto el que
hace viable el cron del paso 9). Ver design.md §Login automático para los riesgos que el
usuario aceptó explícitamente al elegir esta vía.

Nada de aquí devuelve credenciales hacia arriba: solo tokens.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy.auth import laliga_login
from fantasy.auth.token_store import guardar_token, leer_token_descifrado
from fantasy.config import obtener_config
from fantasy.storage.modelos import CredencialesLaLiga

logger = logging.getLogger("fantasy.auth.credenciales_store")


def _fernet() -> Fernet:
    return Fernet(obtener_config().token_encryption_key.encode())


def _buscar(sesion: Session, usuario_id: uuid.UUID) -> CredencialesLaLiga | None:
    return sesion.scalar(
        select(CredencialesLaLiga).where(CredencialesLaLiga.user_id == usuario_id)
    )


def guardar_credenciales(
    sesion: Session, usuario_id: uuid.UUID, email: str, password: str
) -> CredencialesLaLiga:
    """Cifra y guarda. No valida contra LaLiga: eso lo hace quien llama, para poder
    avisar al usuario antes de guardar credenciales que no sirven."""
    fernet = _fernet()
    fila = _buscar(sesion, usuario_id)
    if fila is None:
        fila = CredencialesLaLiga(user_id=usuario_id)
        sesion.add(fila)

    fila.email_cifrado = fernet.encrypt(email.encode())
    fila.password_cifrado = fernet.encrypt(password.encode())
    fila.ultimo_error = None
    sesion.commit()
    sesion.refresh(fila)
    return fila


def tiene_credenciales(sesion: Session, usuario_id: uuid.UUID) -> bool:
    return _buscar(sesion, usuario_id) is not None


def borrar_credenciales(sesion: Session, usuario_id: uuid.UUID) -> bool:
    fila = _buscar(sesion, usuario_id)
    if fila is None:
        return False
    sesion.delete(fila)
    sesion.commit()
    return True


def _descifrar(fila: CredencialesLaLiga) -> tuple[str, str] | None:
    try:
        fernet = _fernet()
        return (
            fernet.decrypt(fila.email_cifrado).decode(),
            fernet.decrypt(fila.password_cifrado).decode(),
        )
    except InvalidToken:
        # Cambió TOKEN_ENCRYPTION_KEY o la fila está corrupta: no es recuperable solo.
        logger.error("no se pudieron descifrar las credenciales de LaLiga del usuario")
        return None


def _guardar_tokens(
    sesion: Session, usuario_id: uuid.UUID, tokens: laliga_login.TokensLaLiga,
    fila: CredencialesLaLiga,
) -> str:
    guardar_token(sesion, usuario_id, tokens.access_token)
    if tokens.refresh_token:
        fila.refresh_token_cifrado = _fernet().encrypt(tokens.refresh_token.encode())
    fila.ultimo_login_ok = datetime.now(timezone.utc)
    fila.ultimo_error = None
    sesion.commit()
    return tokens.access_token


def obtener_token_valido(sesion: Session, usuario_id: uuid.UUID) -> str | None:
    """Devuelve un bearer válido, renovándolo solo si hace falta.

    Orden deliberado, de lo más barato a lo más caro:
      1. Token guardado que aún no ha caducado.
      2. Refresh token (no requiere credenciales).
      3. Login completo con email y contraseña.

    Devuelve None si no hay forma de conseguirlo; quien llama lo trata como token
    inválido y pide al usuario que actúe.
    """
    token = leer_token_descifrado(sesion, usuario_id)
    if token is not None:
        return token

    fila = _buscar(sesion, usuario_id)
    if fila is None:
        return None  # sin credenciales guardadas: solo queda la vía manual

    # 2. Refresh, si lo tenemos.
    if fila.refresh_token_cifrado:
        try:
            refresh = _fernet().decrypt(fila.refresh_token_cifrado).decode()
            return _guardar_tokens(sesion, usuario_id, laliga_login.refrescar(refresh), fila)
        except (InvalidToken, laliga_login.ErrorLoginLaLiga) as exc:
            logger.info("refresh de token fallido, se intentará login completo: %s", type(exc).__name__)

    # 3. Login completo.
    credenciales = _descifrar(fila)
    if credenciales is None:
        fila.ultimo_error = "no se pudieron descifrar las credenciales guardadas"
        sesion.commit()
        return None

    email, password = credenciales
    try:
        tokens = laliga_login.iniciar_sesion(email, password)
    except laliga_login.CredencialesInvalidas:
        # No se reintenta: repetir con credenciales malas no arregla nada y machaca el
        # login de LaLiga. El usuario tiene que actualizarlas.
        fila.ultimo_error = "LaLiga rechazó las credenciales guardadas"
        sesion.commit()
        logger.warning("credenciales de LaLiga rechazadas para un usuario")
        return None
    except laliga_login.ErrorLoginLaLiga as exc:
        fila.ultimo_error = f"login no disponible: {exc}"
        sesion.commit()
        return None

    return _guardar_tokens(sesion, usuario_id, tokens, fila)
