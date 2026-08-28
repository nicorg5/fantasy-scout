"""Sesión de usuario por cookie firmada (ver R7/R8 en requirements.md).

No es JWT ni almacena estado en servidor: la cookie lleva el id de usuario firmado con
`SESSION_SECRET`, así que una cookie alterada falla la verificación de firma.
"""

from __future__ import annotations

import uuid

from itsdangerous import BadSignature, URLSafeTimedSerializer

from fantasy.config import obtener_config

NOMBRE_COOKIE = "fantasy_sesion"
_MAX_EDAD_SEGUNDOS = 60 * 60 * 24 * 30  # 30 días


def _serializador() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(obtener_config().session_secret, salt="sesion")


def crear_valor_cookie(usuario_id: uuid.UUID) -> str:
    return _serializador().dumps(str(usuario_id))


def leer_usuario_id_de_cookie(valor_cookie: str | None) -> uuid.UUID | None:
    if not valor_cookie:
        return None
    try:
        crudo = _serializador().loads(valor_cookie, max_age=_MAX_EDAD_SEGUNDOS)
    except BadSignature:
        return None
    try:
        return uuid.UUID(crudo)
    except ValueError:
        return None
