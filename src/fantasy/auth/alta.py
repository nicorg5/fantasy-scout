"""Alta de un usuario a partir de una invitación (specs/registro, D3).

Una sola transacción: o queda todo (usuario, credenciales, token e invitación usada) o no
queda nada. La lógica no conoce HTTP: devuelve el usuario o lanza, y la ruta traduce.

**Aquí pasan credenciales en claro.** Igual que en `laliga_login`, nada de lo que entra
puede acabar en un log ni en el mensaje de una excepción.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fantasy.auth import invitaciones, laliga_login
from fantasy.auth.credenciales_store import escribir_credenciales, escribir_tokens
from fantasy.auth.passwords import hashear_password, normalizar_email
from fantasy.storage.modelos import Usuario


class EnlaceNoValido(Exception):
    """Invitación inexistente, caducada, usada o revocada. No se distingue el motivo (A8)."""


class YaTieneCuenta(Exception):
    """Ese email ya tiene cuenta: tiene que entrar por /login, no darse de alta (A12)."""


def dar_de_alta(sesion: Session, token: str | None, email: str, password: str) -> Usuario:
    """Crea la cuenta si la invitación es válida y LaLiga acepta las credenciales.

    Lanza `EnlaceNoValido`, `YaTieneCuenta`, `laliga_login.CredencialesInvalidas` o
    `laliga_login.LoginNoDisponible`. En todos los casos la invitación sigue como estaba.

    La fila de la invitación queda bloqueada (`FOR UPDATE`) mientras se consulta a LaLiga:
    es lo que impide que dos altas simultáneas usen el mismo enlace (A13). Solo bloquea
    esa invitación, y a esta escala unos segundos de espera no importan.
    """
    email = normalizar_email(email)
    try:
        invitacion = invitaciones.buscar_pendiente(sesion, token, bloquear=True)
        if invitacion is None:
            raise EnlaceNoValido()
        # Antes de LaLiga: no tiene sentido gastar un login contra ellos para nada.
        if sesion.scalar(select(Usuario.id).where(Usuario.email == email)) is not None:
            raise YaTieneCuenta()

        tokens = laliga_login.iniciar_sesion(email, password)

        usuario = Usuario(email=email, password_hash=hashear_password(password))
        sesion.add(usuario)
        sesion.flush()  # para tener usuario.id
        credenciales = escribir_credenciales(sesion, usuario.id, email, password)
        escribir_tokens(sesion, usuario.id, tokens, credenciales)
        invitaciones.marcar_usada(invitacion, usuario.id)
        sesion.commit()
    except IntegrityError:
        # Otra alta con el mismo email y OTRA invitación ha confirmado justo antes.
        sesion.rollback()
        raise YaTieneCuenta() from None
    except BaseException:
        sesion.rollback()
        raise
    return usuario
