"""Hashing de contraseñas con Argon2. Nunca se implementa hashing propio (ver design.md)."""

from __future__ import annotations

from passlib.context import CryptContext

_contexto = CryptContext(schemes=["argon2"], deprecated="auto")


def hashear_password(password: str) -> str:
    return _contexto.hash(password)


def verificar_password(password: str, password_hash: str) -> bool:
    return _contexto.verify(password, password_hash)
