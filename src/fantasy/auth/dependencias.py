"""Dependencia de FastAPI que exige sesión válida. Sin cookie válida, redirige a /login."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from fantasy.auth.sesiones import NOMBRE_COOKIE, leer_usuario_id_de_cookie
from fantasy.storage.engine import obtener_sesion
from fantasy.storage.modelos import Usuario


class NoAutenticado(Exception):
    """Sin sesión válida. El handler registrado en app.py la convierte en 302 a /login."""


def usuario_actual(
    request: Request, sesion: Session = Depends(obtener_sesion)
) -> Usuario:
    usuario_id = leer_usuario_id_de_cookie(request.cookies.get(NOMBRE_COOKIE))
    if usuario_id is None:
        raise NoAutenticado()

    usuario = sesion.get(Usuario, usuario_id)
    if usuario is None:
        raise NoAutenticado()

    return usuario
