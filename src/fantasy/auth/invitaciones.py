"""Invitaciones de alta de un solo uso (specs/registro, D2).

El token en claro existe **una sola vez**: lo devuelve `crear()` para enseñárselo al admin.
En la base de datos solo queda su SHA-256, así que una fuga no deja enlaces utilizables. No
se usa Argon2: el token ya tiene 256 bits de entropía, un hash lento no aporta nada y
obligaría a recorrer la tabla para buscar.
"""

from __future__ import annotations

import enum
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fantasy.storage.modelos import Invitacion

CADUCIDAD_INVITACION = timedelta(days=7)


class EstadoInvitacion(str, enum.Enum):
    PENDIENTE = "pendiente"
    USADA = "usada"
    CADUCADA = "caducada"
    REVOCADA = "revocada"


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def estado(invitacion: Invitacion, ahora: datetime | None = None) -> EstadoInvitacion:
    """Derivado de las fechas, nunca almacenado. Prioridad: revocada > usada > caducada.

    Una invitación usada sigue siendo "usada" aunque ya haya pasado su caducidad: lo que
    interesa ver en la lista es quién entró con ella.
    """
    if invitacion.revocada_en is not None:
        return EstadoInvitacion.REVOCADA
    if invitacion.usada_en is not None:
        return EstadoInvitacion.USADA
    if invitacion.caduca_en <= (ahora or _ahora()):
        return EstadoInvitacion.CADUCADA
    return EstadoInvitacion.PENDIENTE


def crear(sesion: Session, ahora: datetime | None = None) -> tuple[Invitacion, str]:
    """Crea y confirma una invitación. Devuelve la fila y el token en claro, que no se
    vuelve a poder obtener."""
    ahora = ahora or _ahora()
    token = secrets.token_urlsafe(32)
    invitacion = Invitacion(
        token_hash=hash_token(token), creada_en=ahora, caduca_en=ahora + CADUCIDAD_INVITACION
    )
    sesion.add(invitacion)
    sesion.commit()
    sesion.refresh(invitacion)
    return invitacion, token


def buscar_pendiente(
    sesion: Session, token: str | None, *, bloquear: bool = False, ahora: datetime | None = None
) -> Invitacion | None:
    """La invitación de ese token si está pendiente; None en cualquier otro caso.

    Con `bloquear=True` toma la fila con `FOR UPDATE`: una segunda alta concurrente con el
    mismo enlace espera a que la primera confirme y, al releer, la ve usada (A13). El
    bloqueo dura hasta el commit o rollback de quien llama.
    """
    if not token:
        return None
    consulta = select(Invitacion).where(Invitacion.token_hash == hash_token(token))
    if bloquear:
        consulta = consulta.with_for_update()
    invitacion = sesion.scalar(consulta)
    if invitacion is None or estado(invitacion, ahora) is not EstadoInvitacion.PENDIENTE:
        return None
    return invitacion


def marcar_usada(invitacion: Invitacion, usuario_id: uuid.UUID, ahora: datetime | None = None) -> None:
    """Sin confirmar: forma parte de la transacción del alta (D3)."""
    invitacion.usada_en = ahora or _ahora()
    invitacion.usuario_id = usuario_id


def revocar(sesion: Session, invitacion_id: uuid.UUID, ahora: datetime | None = None) -> bool:
    """Revoca si está pendiente. Revocar una usada o caducada no cambia nada: su historia
    ya está escrita."""
    ahora = ahora or _ahora()
    invitacion = sesion.get(Invitacion, invitacion_id)
    if invitacion is None or estado(invitacion, ahora) is not EstadoInvitacion.PENDIENTE:
        return False
    invitacion.revocada_en = ahora
    sesion.commit()
    return True


def listar(sesion: Session) -> list[Invitacion]:
    """De la más reciente a la más antigua, con el usuario cargado para pintar su email."""
    return list(
        sesion.scalars(
            select(Invitacion)
            .options(selectinload(Invitacion.usuario))
            .order_by(Invitacion.creada_en.desc())
        )
    )
