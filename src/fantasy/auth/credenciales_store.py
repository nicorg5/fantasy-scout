"""Almacenamiento cifrado de las credenciales de LaLiga y obtención automática del token.

Este módulo es el que hace que la app funcione sin intervención diaria (y por tanto el que
hace viable el cron del paso 9). Ver design.md §Login automático para los riesgos que el
usuario aceptó explícitamente al elegir esta vía.

Nada de aquí devuelve credenciales hacia arriba: solo tokens.
"""

from __future__ import annotations

import enum
import logging
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy.auth import laliga_login
from fantasy.auth.passwords import hashear_password
from fantasy.auth.token_store import escribir_token, leer_token_descifrado
from fantasy.config import obtener_config
from fantasy.storage.modelos import CredencialesLaLiga, TipoCredenciales, Usuario

logger = logging.getLogger("fantasy.auth.credenciales_store")

# Lo que queda en `ultimo_error` cuando LaLiga rechaza las credenciales guardadas. Es una
# constante porque también se LEE: decide el aviso de "tu contraseña ha cambiado"
# (specs/registro, D10), y un literal repetido podría divergir en silencio.
ERROR_CREDENCIALES_RECHAZADAS = "LaLiga rechazó las credenciales guardadas"
# Lo mismo para una cuenta social: LaLiga ya no acepta su refresh token (caducado o
# revocado) y solo el usuario puede reconectarla (specs/registro-social, D5).
ERROR_RECONEXION = "LaLiga ya no renueva la conexión: hay que reconectarla"


class EstadoConexion(str, enum.Enum):
    """Lo que hay que decirle al usuario sobre su conexión con LaLiga."""

    SIN_CREDENCIALES = "sin_credenciales"
    CONECTADA = "conectada"
    CONTRASENA_CAMBIADA = "contrasena_cambiada"  # cuenta de contraseña rechazada
    RECONECTAR = "reconectar"  # cuenta social que LaLiga ya no renueva


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
    fila = escribir_credenciales(sesion, usuario_id, email, password)
    sesion.commit()
    sesion.refresh(fila)
    return fila


def escribir_credenciales(
    sesion: Session, usuario_id: uuid.UUID, email: str, password: str
) -> CredencialesLaLiga:
    """Como `guardar_credenciales` pero **sin confirmar** (specs/registro, D4)."""
    fernet = _fernet()
    fila = _buscar(sesion, usuario_id)
    if fila is None:
        fila = CredencialesLaLiga(user_id=usuario_id)
        sesion.add(fila)

    fila.tipo = TipoCredenciales.PASSWORD.value
    fila.email_cifrado = fernet.encrypt(email.encode())
    fila.password_cifrado = fernet.encrypt(password.encode())
    fila.ultimo_error = None
    return fila


def escribir_credenciales_social(
    sesion: Session, usuario_id: uuid.UUID, email: str, oid: str | None
) -> CredencialesLaLiga:
    """Credenciales de una cuenta social, **sin confirmar**: no hay contraseña de LaLiga;
    se renueva solo con el refresh token, que escribe `escribir_tokens`
    (specs/registro-social, D1)."""
    fila = _buscar(sesion, usuario_id)
    if fila is None:
        fila = CredencialesLaLiga(user_id=usuario_id)
        sesion.add(fila)
    fila.tipo = TipoCredenciales.SOCIAL.value
    fila.email_cifrado = _fernet().encrypt(email.encode())
    fila.password_cifrado = None
    fila.laliga_oid = oid
    fila.ultimo_error = None
    return fila


def es_social(fila: CredencialesLaLiga | None) -> bool:
    return fila is not None and fila.tipo == TipoCredenciales.SOCIAL.value


def tiene_credenciales(sesion: Session, usuario_id: uuid.UUID) -> bool:
    return _buscar(sesion, usuario_id) is not None


def buscar_credenciales(sesion: Session, usuario_id: uuid.UUID) -> CredencialesLaLiga | None:
    """La fila tal cual, para quien necesita su tipo u `oid`. Nunca se descifra aquí."""
    return _buscar(sesion, usuario_id)


def estado_conexion(sesion: Session, usuario_id: uuid.UUID) -> EstadoConexion:
    """Qué decirle al usuario: decide el aviso de las páginas y lo que muestra Cuenta.

    Solo los errores que exigen que el usuario actúe cambian el estado; un fallo pasajero
    del login de LaLiga sigue siendo "conectada"."""
    fila = _buscar(sesion, usuario_id)
    if fila is None:
        return EstadoConexion.SIN_CREDENCIALES
    if fila.ultimo_error == ERROR_CREDENCIALES_RECHAZADAS:
        return EstadoConexion.CONTRASENA_CAMBIADA
    if fila.ultimo_error == ERROR_RECONEXION:
        return EstadoConexion.RECONECTAR
    return EstadoConexion.CONECTADA


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


def escribir_tokens(
    sesion: Session, usuario_id: uuid.UUID, tokens: laliga_login.TokensLaLiga,
    fila: CredencialesLaLiga,
) -> None:
    """Guarda bearer y refresh de un login correcto, **sin confirmar** (D4)."""
    escribir_token(sesion, usuario_id, tokens.access_token)
    if tokens.refresh_token:
        fila.refresh_token_cifrado = _fernet().encrypt(tokens.refresh_token.encode())
    fila.ultimo_login_ok = datetime.now(timezone.utc)
    fila.ultimo_error = None


def _guardar_tokens(
    sesion: Session, usuario_id: uuid.UUID, tokens: laliga_login.TokensLaLiga,
    fila: CredencialesLaLiga,
) -> str:
    escribir_tokens(sesion, usuario_id, tokens, fila)
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
    if es_social(fila):
        return _renovar_social(sesion, usuario_id, fila)

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
        fila.ultimo_error = ERROR_CREDENCIALES_RECHAZADAS
        sesion.commit()
        logger.warning("credenciales de LaLiga rechazadas para un usuario")
        return None
    except laliga_login.ErrorLoginLaLiga as exc:
        fila.ultimo_error = f"login no disponible: {exc}"
        sesion.commit()
        return None

    return _guardar_tokens(sesion, usuario_id, tokens, fila)


def _renovar_social(sesion: Session, usuario_id: uuid.UUID, fila: CredencialesLaLiga) -> str | None:
    """Cuenta social: **solo** refresh token, nunca ROPC, porque no hay contraseña
    (specs/registro-social, D5). El refresh rota: `_guardar_tokens` guarda el nuevo."""
    try:
        refresh = _fernet().decrypt(fila.refresh_token_cifrado).decode() if fila.refresh_token_cifrado else None
    except InvalidToken:
        refresh = None  # cambió TOKEN_ENCRYPTION_KEY: no es recuperable sin reconectar
    if refresh is None:
        fila.ultimo_error = ERROR_RECONEXION
        sesion.commit()
        return None

    try:
        tokens = laliga_login.refrescar_social(refresh)
    except laliga_login.CredencialesInvalidas:
        fila.ultimo_error = ERROR_RECONEXION
        sesion.commit()
        logger.warning("LaLiga ya no renueva una cuenta social: hay que reconectarla")
        return None
    except laliga_login.ErrorLoginLaLiga as exc:
        fila.ultimo_error = f"login no disponible: {exc}"
        sesion.commit()
        return None
    return _guardar_tokens(sesion, usuario_id, tokens, fila)


def resincronizar_password(sesion: Session, usuario: Usuario, password: str) -> bool:
    """Cubre "cambié la contraseña en LaLiga" en el login (specs/registro, D6).

    Solo se llama cuando la contraseña NO casa con el hash local. Si el usuario tiene
    credenciales guardadas y LaLiga acepta esta contraseña, es la nueva: se actualizan el
    hash, las credenciales y los tokens, y devuelve True.

    Se prueba con el email de LaLiga **guardado**, no con el que se teclea: puede diferir
    en cuentas creadas con `guardar_credenciales_laliga.py --email-laliga`.

    Una sola llamada, sin reintentos. Un rechazo aquí NO marca las credenciales como
    rechazadas: lo más probable es que el usuario se haya equivocado al teclear.
    """
    fila = _buscar(sesion, usuario.id)
    if fila is None or es_social(fila):
        # Una cuenta social no tiene contraseña de LaLiga contra la que probar (S13).
        return False
    credenciales = _descifrar(fila)
    if credenciales is None:
        return False
    email_laliga, _ = credenciales

    try:
        tokens = laliga_login.iniciar_sesion(email_laliga, password)
    except laliga_login.ErrorLoginLaLiga:
        return False

    usuario.password_hash = hashear_password(password)
    escribir_credenciales(sesion, usuario.id, email_laliga, password)
    escribir_tokens(sesion, usuario.id, tokens, fila)
    sesion.commit()
    logger.info("contraseña resincronizada con LaLiga en el login")  # sin email: dato personal
    return True
