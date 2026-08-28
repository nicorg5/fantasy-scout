"""Engine y factoría de sesiones contra Postgres.

`DATABASE_URL` es el único punto de configuración: se lee de `config.py`, nunca aquí
directamente, para que el secreto siga teniendo una sola fuente (ver R46).
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from fantasy.config import obtener_config


@lru_cache(maxsize=1)
def obtener_engine() -> Engine:
    config = obtener_config()
    return create_engine(config.database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def obtener_fabrica_sesiones() -> sessionmaker[Session]:
    return sessionmaker(bind=obtener_engine(), expire_on_commit=False)


def obtener_sesion() -> Iterator[Session]:
    """Dependencia de FastAPI: una sesión por request, cerrada al terminar."""
    fabrica = obtener_fabrica_sesiones()
    sesion = fabrica()
    try:
        yield sesion
    finally:
        sesion.close()
