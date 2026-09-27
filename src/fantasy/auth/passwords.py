"""Hashing de contraseñas con Argon2. Nunca se implementa hashing propio (ver design.md)."""

from __future__ import annotations

from passlib.context import CryptContext

_contexto = CryptContext(schemes=["argon2"], deprecated="auto")


def hashear_password(password: str) -> str:
    return _contexto.hash(password)


def verificar_password(password: str, password_hash: str) -> bool:
    return _contexto.verify(password, password_hash)


def normalizar_email(email: str) -> str:
    """Forma canónica de un email: sin espacios y en minúsculas (A15 de specs/registro).

    Todas las escrituras y búsquedas de `user.email` pasan por aquí, así que el `unique`
    de la columna basta para que `Pepe@x.com` y `pepe@x.com` sean la misma cuenta.
    """
    return email.strip().lower()
