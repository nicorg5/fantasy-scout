"""Fixtures compartidas. Los tests de auth usan el Postgres local (docker-compose.yml)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from fantasy.api.app import app
from fantasy.auth.passwords import hashear_password
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import Usuario


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


@pytest.fixture
def jwt_falso():
    """Fabrica un JWT sin firmar con el `exp` pedido.

    Sirve para probar el manejo de caducidad sin usar un token real de LaLiga: solo se
    lee el payload, nunca se valida la firma.
    """

    def _crear(exp: datetime) -> str:
        carga = (
            base64.urlsafe_b64encode(json.dumps({"exp": int(exp.timestamp())}).encode())
            .decode()
            .rstrip("=")
        )
        return f"cabecera.{carga}.firma"

    return _crear


@pytest.fixture
def sesion_db():
    fabrica = obtener_fabrica_sesiones()
    with fabrica() as sesion:
        yield sesion


@pytest.fixture
def usuario_de_prueba(sesion_db: Session):
    """Crea un usuario efímero y lo borra al terminar el test, sea cual sea el resultado."""
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    password = "una-password-de-prueba-123"
    usuario = Usuario(email=email, password_hash=hashear_password(password))
    sesion_db.add(usuario)
    sesion_db.commit()
    sesion_db.refresh(usuario)

    yield usuario, password

    sesion_db.delete(sesion_db.get(Usuario, usuario.id))
    sesion_db.commit()


@pytest.fixture
def cliente_autenticado(cliente: TestClient, usuario_de_prueba):
    usuario, password = usuario_de_prueba
    respuesta = cliente.post(
        "/login",
        data={"email": usuario.email, "password": password},
        follow_redirects=False,
    )
    assert respuesta.status_code == 302, respuesta.text
    return cliente


@pytest.fixture
def mapa_equipos_falso(monkeypatch):
    """Mapa de equipos mínimo y estable para los tests de matching.

    Se parchea en vez de leer `data/mappings/equipos.json` para que los tests no dependan
    de que ese fichero se haya regenerado, ni cambien de resultado cuando se regenere.
    Refleja el caso real: Málaga es 12 en oficial y 11 en futbolfantasy.
    """
    from fantasy.matching import equipos

    original = equipos.cargar_mapa_equipos
    original.cache_clear()
    monkeypatch.setattr(
        equipos,
        "cargar_mapa_equipos",
        lambda: {
            "12": {"id": "11", "slug": "malaga"},
            "17": {"id": "17", "slug": "sevilla"},
        },
    )
    yield
    # Sobre la original: en este punto el monkeypatch aún no ha restaurado el atributo.
    original.cache_clear()
