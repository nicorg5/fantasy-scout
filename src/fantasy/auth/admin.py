"""Autorización del administrador: la única cuenta que ve Uso e Invitación.

Vivía en `observabilidad/rutas.py` mientras solo la usaba `/uso`; con `/invitaciones` pasa a
ser autorización y se mueve aquí (specs/registro, D1). La dependencia va siempre de
`observabilidad` hacia `auth`, nunca al revés.

El admin se identifica por `FANTASY_ADMIN_EMAIL`. Con la variable vacía nadie lo es: fallo
cerrado por defecto.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Depends, Request
from sqlalchemy import select

from fantasy.auth.dependencias import usuario_actual
from fantasy.auth.passwords import normalizar_email
from fantasy.auth.sesiones import NOMBRE_COOKIE, leer_usuario_id_de_cookie
from fantasy.config import obtener_config
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import Usuario

logger = logging.getLogger("fantasy.auth.admin")


class NoAdministrador(Exception):
    """Un usuario con sesión pide una pantalla de admin. El handler de `app.py` responde
    302 a `/plantilla`: ni un 404 ni un 403, el usuario no debe notar que ahí hay algo
    (specs/registro, A3)."""


# id del admin por email. No se guarda un "no existe": el admin puede crearse después.
_ids_admin: dict[str, uuid.UUID] = {}


def es_admin(request: Request | None) -> bool:
    """Si quien navega es el administrador. **Solo decide si se pinta el enlace del menú**:
    la protección de verdad es la dependencia `administrador` de la ruta.

    Se llama en cada página, así que no puede costar ni romper nada: el id del admin se
    busca una vez por proceso, y cualquier fallo significa simplemente "no pintar".
    """
    admin = obtener_config().admin_email
    if not admin or request is None:
        return False
    usuario_id = leer_usuario_id_de_cookie(request.cookies.get(NOMBRE_COOKIE))
    if usuario_id is None:
        return False

    if admin not in _ids_admin:
        try:
            with obtener_fabrica_sesiones()() as sesion:
                encontrado = sesion.scalar(select(Usuario.id).where(Usuario.email == admin))
        except Exception as exc:  # noqa: BLE001 - un enlace de menú no tumba una página
            logger.warning("no se pudo comprobar si el usuario es admin: %s", exc)
            return False
        if encontrado is None:
            return False
        _ids_admin[admin] = encontrado

    return usuario_id == _ids_admin[admin]


def administrador(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    """Deja pasar solo a `FANTASY_ADMIN_EMAIL`. Sin sesión, `usuario_actual` ya redirige a
    /login; con sesión y sin ser admin, `NoAdministrador` redirige a /plantilla."""
    admin = obtener_config().admin_email
    if not admin or normalizar_email(usuario.email) != admin:
        raise NoAdministrador()
    return usuario
