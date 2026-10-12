"""Intentos de conexión social, entre el enlace a LaLiga y la dirección pegada
(specs/registro-social, D2).

Cada intento guarda su verificador PKCE cifrado y caduca a los 15 minutos. Se busca por el
`state` que viaja en la dirección de vuelta, así que funciona aunque el usuario pegue en
otro navegador. Se borra en la misma transacción que crea la cuenta: si el canje falla,
sigue vivo hasta caducar, y el usuario puede reabrir el mismo enlace de LaLiga.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from fantasy.auth import laliga_login
from fantasy.config import obtener_config
from fantasy.storage.modelos import IntentoConexion

CADUCIDAD_INTENTO = timedelta(minutes=15)


def _fernet() -> Fernet:
    return Fernet(obtener_config().token_encryption_key.encode())


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def crear(
    sesion: Session,
    *,
    invitacion_id: uuid.UUID | None = None,
    usuario_id: uuid.UUID | None = None,
    ahora: datetime | None = None,
) -> tuple[IntentoConexion, str]:
    """Crea un intento para una invitación (alta) o un usuario (reconexión) y devuelve el
    enlace al login de LaLiga. Borra el intento anterior de ese mismo dueño (S3) y, de paso,
    los caducados de cualquiera: es toda la limpieza que hace falta."""
    if (invitacion_id is None) == (usuario_id is None):
        raise ValueError("un intento es de una invitación o de un usuario, nunca de ambos")
    ahora = ahora or _ahora()
    dueno = (
        IntentoConexion.invitacion_id == invitacion_id
        if invitacion_id is not None
        else IntentoConexion.usuario_id == usuario_id
    )
    sesion.execute(delete(IntentoConexion).where(or_(dueno, IntentoConexion.caduca_en <= ahora)))

    state = secrets.token_urlsafe(24)
    verificador = secrets.token_urlsafe(64)
    intento = IntentoConexion(
        state=state,
        verificador_cifrado=_fernet().encrypt(verificador.encode()),
        invitacion_id=invitacion_id,
        usuario_id=usuario_id,
        caduca_en=ahora + CADUCIDAD_INTENTO,
    )
    sesion.add(intento)
    sesion.commit()
    return intento, laliga_login.url_login_social(state, verificador)


def buscar_vigente(sesion: Session, state: str | None, ahora: datetime | None = None) -> IntentoConexion | None:
    if not state:
        return None
    intento = sesion.scalar(select(IntentoConexion).where(IntentoConexion.state == state))
    if intento is None or intento.caduca_en <= (ahora or _ahora()):
        return None
    return intento


def verificador(intento: IntentoConexion) -> str:
    return _fernet().decrypt(intento.verificador_cifrado).decode()


def consumir(sesion: Session, intento: IntentoConexion) -> str:
    """Devuelve el verificador y borra el intento, **sin confirmar**.

    Sin commit a propósito: quien llama tiene bloqueada la invitación (`FOR UPDATE`) y un
    commit aquí soltaría ese bloqueo antes de canjear, abriendo la puerta a dos altas con el
    mismo enlace. Si el canje falla, el rollback devuelve el intento: no es un riesgo, porque
    el código de LaLiga es de un solo uso igualmente (ver Descubrimientos de T5)."""
    valor = verificador(intento)
    sesion.delete(intento)
    return valor
