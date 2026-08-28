"""Cifrado y persistencia del bearer token de LaLiga Fantasy de cada usuario.

El token se descifra únicamente en memoria, en el servidor, justo antes de usarlo contra
la API oficial (paso 3). Nunca se expone al frontend (ver R12 en requirements.md).
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy.config import obtener_config
from fantasy.storage.modelos import TokenUsuario

# Vigencia real del bearer de LaLiga, VERIFICADA decodificando el claim `exp` de un token
# propio (2026-08-26): exp - iat = 86400 s exactos. Se usa como respaldo cuando no se puede
# leer el `exp` del token concreto.
VIGENCIA_POR_DEFECTO = timedelta(hours=24)


def _fernet() -> Fernet:
    clave = obtener_config().token_encryption_key
    return Fernet(clave.encode())


def expiracion_del_jwt(token: str) -> datetime | None:
    """Lee el claim `exp` del bearer de LaLiga, que es un JWT.

    Importa porque el usuario pega el token cuando ya lleva un rato emitido: asumir
    "24h desde que lo pegó" daría por válido un token que en LaLiga ya caducó. Si el
    token no es un JWT legible se devuelve None y quien llama aplica el respaldo.
    """
    partes = token.split(".")
    if len(partes) != 3:
        return None
    carga = partes[1]
    carga += "=" * (-len(carga) % 4)  # el padding base64url viene recortado
    try:
        datos = json.loads(base64.urlsafe_b64decode(carga))
        exp = datos["exp"]
    except (ValueError, KeyError, TypeError):
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc)


def _buscar_por_usuario(sesion: Session, usuario_id: uuid.UUID) -> TokenUsuario | None:
    return sesion.scalar(select(TokenUsuario).where(TokenUsuario.user_id == usuario_id))


def guardar_token(sesion: Session, usuario_id: uuid.UUID, token_en_claro: str) -> TokenUsuario:
    """Cifra y guarda el token, sustituyendo el anterior si ya existía uno."""
    cifrado = _fernet().encrypt(token_en_claro.encode())
    # La caducidad real del propio token manda; el respaldo solo cubre el caso de que
    # LaLiga deje de emitir JWT o cambie el formato.
    expira_en = expiracion_del_jwt(token_en_claro) or (
        datetime.now(timezone.utc) + VIGENCIA_POR_DEFECTO
    )

    fila = _buscar_por_usuario(sesion, usuario_id)
    if fila is None:
        fila = TokenUsuario(user_id=usuario_id, token_cifrado=cifrado, expira_en=expira_en)
        sesion.add(fila)
    else:
        fila.token_cifrado = cifrado
        fila.expira_en = expira_en

    sesion.commit()
    sesion.refresh(fila)
    return fila


def leer_token_descifrado(sesion: Session, usuario_id: uuid.UUID) -> str | None:
    """Devuelve el token en claro, o None si no hay token guardado o está corrupto/expirado.

    Nunca lanza sobre un fallo de descifrado: un token corrupto se trata igual que "no
    hay token", y quien llama decide pedir que se repegue (ver R13).
    """
    fila = _buscar_por_usuario(sesion, usuario_id)
    if fila is None:
        return None
    if fila.expira_en <= datetime.now(timezone.utc):
        return None
    try:
        return _fernet().decrypt(fila.token_cifrado).decode()
    except InvalidToken:
        return None


def token_expirado(sesion: Session, usuario_id: uuid.UUID) -> bool:
    fila = _buscar_por_usuario(sesion, usuario_id)
    if fila is None:
        return True
    return fila.expira_en <= datetime.now(timezone.utc)
