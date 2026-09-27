"""Invitaciones de alta (specs/registro, bloque A)."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

from fantasy.auth import invitaciones
from fantasy.auth.invitaciones import EstadoInvitacion
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.fechas import MADRID
from fantasy.storage.modelos import Invitacion

_MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/2f3a087ef0d2_invitacion.py"


def _cargar_migracion():
    spec = importlib.util.spec_from_file_location("migracion_invitacion", _MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_tabla_invitacion_no_guarda_el_token_en_claro(sesion_db):
    """A1"""
    columnas = {c["name"] for c in inspect(sesion_db.get_bind()).get_columns("invitacion")}
    assert columnas == {
        "id", "token_hash", "creada_en", "caduca_en", "usada_en", "usuario_id", "revocada_en",
    }
    fks = inspect(sesion_db.get_bind()).get_foreign_keys("invitacion")
    assert fks[0]["referred_table"] == "user"
    assert fks[0]["options"]["ondelete"] == "SET NULL"


def _insertar_usuario(conexion, email: str) -> None:
    conexion.execute(
        text('INSERT INTO "user" (id, email, password_hash) VALUES (:id, :email, \'x\')'),
        {"id": uuid.uuid4(), "email": email},
    )


def test_la_migracion_normaliza_los_emails():
    """A15. Todo dentro de una transacción que se deshace: no deja rastro."""
    sufijo = uuid.uuid4().hex[:8]
    with obtener_fabrica_sesiones()() as sesion:
        conexion = sesion.connection()
        _insertar_usuario(conexion, f"  Mayus-{sufijo}@X.com ")
        _cargar_migracion().normalizar_emails(conexion)
        emails = conexion.execute(
            text('SELECT email FROM "user" WHERE email LIKE :p'), {"p": f"%{sufijo}%"}
        ).scalars().all()
        sesion.rollback()
    assert emails == [f"mayus-{sufijo}@x.com"]


def test_la_migracion_aborta_si_quedarian_duplicados():
    """A15: fusionar cuentas es una decisión humana."""
    sufijo = uuid.uuid4().hex[:8]
    with obtener_fabrica_sesiones()() as sesion:
        conexion = sesion.connection()
        _insertar_usuario(conexion, f"A-{sufijo}@x.com")
        _insertar_usuario(conexion, f"a-{sufijo}@x.com")
        with pytest.raises(RuntimeError, match="duplicados"):
            _cargar_migracion().normalizar_emails(conexion)
        # No ha escrito nada: los dos siguen tal cual.
        emails = sorted(conexion.execute(
            text('SELECT email FROM "user" WHERE email LIKE :p'), {"p": f"%{sufijo}%"}
        ).scalars().all())
        sesion.rollback()
    assert emails == [f"A-{sufijo}@x.com", f"a-{sufijo}@x.com"]


# --------------------------------------------------------------------------------------
# T5: lógica (A4-A6)


@pytest.fixture
def borrar_invitaciones(sesion_db):
    """Borra al terminar las invitaciones que cree el test."""
    creadas: list[uuid.UUID] = []
    yield creadas
    for invitacion_id in creadas:
        fila = sesion_db.get(Invitacion, invitacion_id)
        if fila is not None:
            sesion_db.delete(fila)
    sesion_db.commit()


@pytest.fixture
def nueva_invitacion(sesion_db, borrar_invitaciones):
    def _crear(ahora: datetime | None = None) -> tuple[Invitacion, str]:
        invitacion, token = invitaciones.crear(sesion_db, ahora=ahora)
        borrar_invitaciones.append(invitacion.id)
        return invitacion, token

    return _crear


def test_crear_caduca_a_los_7_dias_y_guarda_solo_el_hash(nueva_invitacion):
    """A4"""
    invitacion, token = nueva_invitacion()
    assert invitacion.caduca_en - invitacion.creada_en == timedelta(days=7)
    assert invitacion.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert len(token) >= 43  # 32 bytes en base64url: 256 bits


def test_dos_invitaciones_no_comparten_token(nueva_invitacion):
    _, uno = nueva_invitacion()
    _, otro = nueva_invitacion()
    assert uno != otro


def _invitacion(**fechas) -> Invitacion:
    ahora = datetime(2026, 9, 27, tzinfo=timezone.utc)
    base = {"caduca_en": ahora + timedelta(days=1), "usada_en": None, "revocada_en": None}
    base.update(fechas)
    return Invitacion(token_hash="x", creada_en=ahora, **base)


@pytest.mark.parametrize(
    ("fechas", "esperado"),
    [
        ({}, EstadoInvitacion.PENDIENTE),
        ({"caduca_en": datetime(2026, 9, 26, tzinfo=timezone.utc)}, EstadoInvitacion.CADUCADA),
        ({"usada_en": datetime(2026, 9, 27, tzinfo=timezone.utc)}, EstadoInvitacion.USADA),
        (
            {"usada_en": datetime(2026, 9, 27, tzinfo=timezone.utc),
             "caduca_en": datetime(2026, 9, 26, tzinfo=timezone.utc)},
            EstadoInvitacion.USADA,
        ),
        (
            {"revocada_en": datetime(2026, 9, 27, tzinfo=timezone.utc),
             "caduca_en": datetime(2026, 9, 26, tzinfo=timezone.utc)},
            EstadoInvitacion.REVOCADA,
        ),
    ],
)
def test_estado_y_su_prioridad(fechas, esperado):
    """A5: revocada > usada > caducada > pendiente."""
    assert invitaciones.estado(_invitacion(**fechas), ahora=datetime(2026, 9, 27, tzinfo=timezone.utc)) is esperado


def test_buscar_pendiente_solo_devuelve_pendientes(sesion_db, nueva_invitacion):
    invitacion, token = nueva_invitacion()
    assert invitaciones.buscar_pendiente(sesion_db, token).id == invitacion.id
    assert invitaciones.buscar_pendiente(sesion_db, "token-inventado") is None
    assert invitaciones.buscar_pendiente(sesion_db, None) is None

    caducada, token_caducado = nueva_invitacion(ahora=datetime.now(timezone.utc) - timedelta(days=8))
    assert invitaciones.buscar_pendiente(sesion_db, token_caducado) is None


def test_revocar_una_pendiente_la_inutiliza(sesion_db, nueva_invitacion):
    """A6"""
    invitacion, token = nueva_invitacion()
    assert invitaciones.revocar(sesion_db, invitacion.id) is True
    assert invitaciones.estado(invitacion) is EstadoInvitacion.REVOCADA
    assert invitaciones.buscar_pendiente(sesion_db, token) is None


def test_revocar_una_no_pendiente_no_cambia_nada(sesion_db, nueva_invitacion):
    invitacion, _ = nueva_invitacion(ahora=datetime.now(timezone.utc) - timedelta(days=8))
    assert invitaciones.revocar(sesion_db, invitacion.id) is False
    assert invitacion.revocada_en is None
    assert invitaciones.revocar(sesion_db, uuid.uuid4()) is False


def test_listar_de_la_mas_reciente_a_la_mas_antigua(sesion_db, nueva_invitacion):
    antigua, _ = nueva_invitacion(ahora=datetime.now(timezone.utc) - timedelta(days=2))
    reciente, _ = nueva_invitacion()
    ids = [i.id for i in invitaciones.listar(sesion_db)]
    assert ids.index(reciente.id) < ids.index(antigua.id)


# --------------------------------------------------------------------------------------
# T6: pantalla (A2-A6)


def _enlaces_admin(html: str) -> set[str]:
    return {r for r in ("/uso", "/invitaciones") if f'href="{r}"' in html}


def test_el_menu_de_admin_tiene_uso_e_invitacion(cliente_autenticado, usuario_de_prueba, admin_es):
    """A2"""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    html = cliente_autenticado.get("/token").text
    assert _enlaces_admin(html) == {"/uso", "/invitaciones"}
    assert ">Cuenta</a>" in html


def test_un_usuario_normal_no_ve_ninguna_seccion_de_admin(otro_cliente, usuario_de_prueba, admin_es):
    """A2: el HTML no menciona ninguna de las dos."""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    html = otro_cliente.get("/token").text
    assert "/invitaciones" not in html
    assert 'href="/uso"' not in html


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("get", "/uso"),
        ("get", "/invitaciones"),
        ("post", "/invitaciones"),
        ("post", f"/invitaciones/{uuid.uuid4()}/revocar"),
    ],
)
def test_un_usuario_normal_vuelve_a_su_plantilla(otro_cliente, usuario_de_prueba, admin_es, sesion_db, metodo, ruta):
    """A3: ni 404 ni 403. Y un POST no llega a crear nada."""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    antes = sesion_db.query(Invitacion).count()

    respuesta = getattr(otro_cliente, metodo)(ruta, follow_redirects=False)

    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/plantilla"
    assert sesion_db.query(Invitacion).count() == antes


def test_sin_sesion_invitaciones_redirige_al_login(cliente, admin_es):
    admin_es("alguien@example.com")
    respuesta = cliente.get("/invitaciones", follow_redirects=False)
    assert respuesta.headers["location"] == "/login"


def test_sin_admin_configurado_nadie_crea_invitaciones(cliente_autenticado, admin_es):
    admin_es("")
    respuesta = cliente_autenticado.post("/invitaciones", follow_redirects=False)
    assert respuesta.headers["location"] == "/plantilla"


def _token_del_enlace(html: str) -> str:
    return re.search(r"/registro\?invitacion=([A-Za-z0-9_-]+)", html).group(1)


def test_crear_invitacion_muestra_el_enlace(cliente_autenticado, usuario_de_prueba, admin_es, sesion_db, borrar_invitaciones):
    """A4"""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)

    respuesta = cliente_autenticado.post("/invitaciones")

    assert respuesta.status_code == 200
    token = _token_del_enlace(respuesta.text)
    invitacion = sesion_db.scalar(
        select(Invitacion).where(Invitacion.token_hash == hashlib.sha256(token.encode()).hexdigest())
    )
    borrar_invitaciones.append(invitacion.id)
    assert "http://testserver/registro?invitacion=" in respuesta.text  # local: sin forzar https


def test_fuera_de_local_el_enlace_es_https(usuario_de_prueba, admin_es, monkeypatch):
    """D8: detrás del proxy de Render uvicorn ve `http`."""
    usuario, _ = usuario_de_prueba
    monkeypatch.setenv("FANTASY_ENTORNO", "produccion")
    admin_es(usuario.email)  # vacía la caché de config: recoge también el entorno

    # En producción la cookie es Secure y el TestClient no la manda por http: se usa la
    # función directamente, que es lo que decide el esquema.
    from starlette.requests import Request

    from fantasy.auth.rutas import url_publica

    request = Request({"type": "http", "scheme": "http", "server": ("fantasy-scout.onrender.com", 80),
                       "path": "/", "headers": [(b"host", b"fantasy-scout.onrender.com")]})
    assert url_publica(request, "/registro?invitacion=abc") == "https://fantasy-scout.onrender.com/registro?invitacion=abc"


def test_la_lista_muestra_cada_estado_y_quien_la_uso(cliente_autenticado, usuario_de_prueba, admin_es, nueva_invitacion, sesion_db):
    """A5"""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    ahora = datetime.now(timezone.utc)
    nueva_invitacion()
    nueva_invitacion(ahora=ahora - timedelta(days=8))
    usada, _ = nueva_invitacion()
    invitaciones.marcar_usada(usada, usuario.id)
    revocada, _ = nueva_invitacion()
    invitaciones.revocar(sesion_db, revocada.id)
    sesion_db.commit()

    html = cliente_autenticado.get("/invitaciones").text

    for estado in ("pendiente", "caducada", "usada", "revocada"):
        assert f"estado-invitacion--{estado}" in html
    assert usuario.email in html
    # Cuándo se usó, no solo quién (hora de Madrid, formato del filtro `madrid`).
    sesion_db.refresh(usada)
    assert f"el {usada.usada_en.astimezone(MADRID).strftime('%d/%m %H:%M')}" in html


def test_revocar_desde_la_pantalla(cliente_autenticado, usuario_de_prueba, admin_es, nueva_invitacion, sesion_db):
    """A6"""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    invitacion, token = nueva_invitacion()

    respuesta = cliente_autenticado.post(f"/invitaciones/{invitacion.id}/revocar", follow_redirects=False)

    assert respuesta.headers["location"] == "/invitaciones"
    sesion_db.expire_all()
    assert invitaciones.buscar_pendiente(sesion_db, token) is None
