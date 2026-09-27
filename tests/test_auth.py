"""Bloque B — auth y token (paso 2). Ver requirements.md."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from fantasy.auth import laliga_login
from fantasy.auth.credenciales_store import _buscar, _descifrar, guardar_credenciales
from fantasy.auth.passwords import verificar_password
from fantasy.storage.modelos import Usuario


def test_sin_invitacion_no_hay_alta(cliente, sesion_db):
    """R6 (reescrito por specs/registro): solo se entra con una invitación válida."""
    email = f"sin-invitacion-{uuid.uuid4().hex[:8]}@example.com"
    respuesta = cliente.post("/registro", data={"email": email, "password": "x"})
    assert respuesta.status_code == 400
    assert sesion_db.scalar(select(Usuario).where(Usuario.email == email)) is None


def test_password_nunca_en_claro(usuario_de_prueba):
    """R9: la columna guarda un hash Argon2, no la contraseña."""
    usuario, password = usuario_de_prueba
    assert usuario.password_hash != password
    assert usuario.password_hash.startswith("$argon2")


def test_login_correcto_da_cookie_de_sesion_segura(cliente, usuario_de_prueba):
    """R7: cookie firmada, HttpOnly + Secure + SameSite=Lax."""
    usuario, password = usuario_de_prueba
    respuesta = cliente.post(
        "/login",
        data={"email": usuario.email, "password": password},
        follow_redirects=False,
    )
    assert respuesta.status_code == 302

    set_cookie = respuesta.headers["set-cookie"]
    assert "fantasy_sesion=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    # Secure se activa solo fuera de entorno local (ver design.md); en local no debe
    # bloquear el guardado de la cookie por el navegador sobre HTTP.


def test_login_incorrecto_no_da_sesion(cliente, usuario_de_prueba):
    usuario, _ = usuario_de_prueba
    respuesta = cliente.post(
        "/login",
        data={"email": usuario.email, "password": "password-equivocada"},
    )
    assert respuesta.status_code == 401
    assert "set-cookie" not in respuesta.headers


@pytest.mark.parametrize("ruta", ["/plantilla", "/mercado"])
def test_rutas_protegidas_redirigen_a_login_sin_cookie(cliente, ruta):
    """R8"""
    respuesta = cliente.get(ruta, follow_redirects=False)
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/login"


def test_logout_borra_la_cookie(cliente_autenticado):
    respuesta = cliente_autenticado.post("/logout", follow_redirects=False)
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/login"

    respuesta = cliente_autenticado.get("/plantilla", follow_redirects=False)
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/login"


def test_normalizar_email():
    """A15 (specs/registro)."""
    from fantasy.auth.passwords import normalizar_email

    assert normalizar_email("  Pepe@X.com ") == "pepe@x.com"


def test_login_ignora_mayusculas_y_espacios_del_email(cliente, usuario_de_prueba):
    """A15: la cuenta es la misma escriba el email como lo escriba."""
    usuario, password = usuario_de_prueba
    respuesta = cliente.post(
        "/login",
        data={"email": f"  {usuario.email.upper()} ", "password": password},
        follow_redirects=False,
    )
    assert respuesta.status_code == 302


# --------------------------------------------------------------------------------------
# specs/registro, D6: resincronización con LaLiga en el login (A16, A17)

NUEVA = "la-nueva-de-laliga-456"


@pytest.fixture
def laliga(monkeypatch, jwt_falso):
    """LaLiga falso: registra con qué email se le llama y responde lo que se le diga."""
    estado = {"llamadas": [], "lanza": None}

    def _iniciar(email, password):
        estado["llamadas"].append(email)
        if estado["lanza"] is not None:
            raise estado["lanza"]
        exp = datetime.now(timezone.utc) + timedelta(hours=24)
        return laliga_login.TokensLaLiga(jwt_falso(exp), "refresh", exp)

    monkeypatch.setattr(laliga_login, "iniciar_sesion", _iniciar)
    return estado


def _login(cliente, email, password):
    return cliente.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def test_login_con_email_inexistente_no_llama_a_laliga(cliente, laliga):
    assert _login(cliente, f"nadie-{uuid.uuid4().hex[:6]}@example.com", NUEVA).status_code == 401
    assert laliga["llamadas"] == []


def test_login_con_la_contrasena_de_siempre_no_llama_a_laliga(cliente, usuario_de_prueba, sesion_db, laliga):
    usuario, password = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, "laliga@example.com", password)
    assert _login(cliente, usuario.email, password).status_code == 302
    assert laliga["llamadas"] == []


def test_login_sin_credenciales_guardadas_no_llama_a_laliga(cliente, usuario_de_prueba, laliga):
    """A17"""
    usuario, _ = usuario_de_prueba
    assert _login(cliente, usuario.email, NUEVA).status_code == 401
    assert laliga["llamadas"] == []


def test_contrasena_cambiada_en_laliga_se_resincroniza(cliente, usuario_de_prueba, sesion_db, laliga):
    """A16: se prueba con el email de LaLiga GUARDADO y todo queda actualizado."""
    usuario, password = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, "laliga@example.com", password)

    assert _login(cliente, usuario.email, NUEVA).status_code == 302

    assert laliga["llamadas"] == ["laliga@example.com"]
    sesion_db.expire_all()
    assert verificar_password(NUEVA, sesion_db.get(Usuario, usuario.id).password_hash)
    assert _descifrar(_buscar(sesion_db, usuario.id)) == ("laliga@example.com", NUEVA)
    # Y a la siguiente ya entra por el camino normal, sin LaLiga.
    assert _login(cliente, usuario.email, NUEVA).status_code == 302
    assert len(laliga["llamadas"]) == 1


@pytest.mark.parametrize(
    "excepcion",
    [laliga_login.CredencialesInvalidas("x"), laliga_login.LoginNoDisponible("x")],
    ids=["rechaza", "no_responde"],
)
def test_si_laliga_no_acepta_el_login_falla_y_nada_cambia(cliente, usuario_de_prueba, sesion_db, laliga, excepcion):
    """A17: mismo 401 genérico. Un error de tecleo no marca las credenciales como rechazadas."""
    usuario, password = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, "laliga@example.com", password)
    hash_antes = usuario.password_hash
    laliga["lanza"] = excepcion

    respuesta = _login(cliente, usuario.email, NUEVA)

    assert respuesta.status_code == 401
    assert "Email o contraseña incorrectos" in respuesta.text
    assert len(laliga["llamadas"]) == 1
    sesion_db.expire_all()
    assert sesion_db.get(Usuario, usuario.id).password_hash == hash_antes
    fila = _buscar(sesion_db, usuario.id)
    assert _descifrar(fila) == ("laliga@example.com", password)
    assert fila.ultimo_error is None
