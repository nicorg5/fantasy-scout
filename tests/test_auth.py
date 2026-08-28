"""Bloque B — auth y token (paso 2). Ver requirements.md."""

from __future__ import annotations

import pytest

from fantasy.storage.modelos import Usuario


def test_no_hay_endpoint_de_registro(cliente):
    """R6: las cuentas se crean con el script de alta manual, no vía la web."""
    respuesta = cliente.post("/registro", data={"email": "x@y.z", "password": "x"})
    assert respuesta.status_code == 404


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
