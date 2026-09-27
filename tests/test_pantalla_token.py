"""R10 y R12: pantalla de token con instrucciones, y el valor nunca sale al frontend."""

from __future__ import annotations


def test_formulario_token_exige_sesion(cliente):
    respuesta = cliente.get("/token", follow_redirects=False)
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/login"


def test_formulario_token_tiene_instrucciones(cliente_autenticado):
    html = cliente_autenticado.get("/token").text
    assert "DevTools" in html or "herramientas de desarrollador" in html.lower()
    assert "Bearer" in html


def test_token_guardado_no_aparece_en_la_respuesta(cliente_autenticado):
    """R12: el valor pegado nunca vuelve en el HTML."""
    token_secreto = "mi-bearer-de-laliga-muy-secreto-12345"
    respuesta = cliente_autenticado.post("/token", data={"token": token_secreto})

    assert respuesta.status_code == 200
    assert token_secreto not in respuesta.text
    assert "Token guardado correctamente" in respuesta.text


def test_conectar_cuenta_valida_antes_de_guardar(cliente_autenticado):
    """Credenciales malas: no se guardan y se avisa en el momento."""
    from unittest.mock import patch

    from fantasy.auth import laliga_login

    with patch("fantasy.auth.rutas.laliga_login.iniciar_sesion",
               side_effect=laliga_login.CredencialesInvalidas("no")):
        respuesta = cliente_autenticado.post(
            "/credenciales",
            data={"email_laliga": "x@y.z", "password_laliga": "mala"},
            follow_redirects=False,
        )

    assert respuesta.status_code == 400
    assert "rechazado" in respuesta.text


def test_la_password_de_laliga_nunca_vuelve_en_la_respuesta(cliente_autenticado):
    """R12 extendido a las credenciales: no pueden aparecer en el HTML."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    from fantasy.auth.laliga_login import TokensLaLiga

    secreta = "password-super-secreta-9876"
    tokens = TokensLaLiga("tok", "ref", datetime.now(timezone.utc) + timedelta(hours=24))

    with patch("fantasy.auth.rutas.laliga_login.iniciar_sesion", return_value=tokens):
        with patch("fantasy.auth.credenciales_store.laliga_login.iniciar_sesion", return_value=tokens):
            respuesta = cliente_autenticado.post(
                "/credenciales",
                data={"email_laliga": "x@y.z", "password_laliga": secreta},
            )

    assert secreta not in respuesta.text


# --------------------------------------------------------------------------------------
# specs/registro, A18-A19


def test_la_pantalla_es_la_cuenta_de_laliga_y_el_token_va_plegado(cliente_autenticado):
    """A18: lo manual sigue ahí, pero cerrado."""
    html = cliente_autenticado.get("/token").text
    assert "<h1>Tu cuenta de LaLiga</h1>" in html
    assert "<details>" in html and "<details open" not in html
    assert html.index('action="/credenciales"') < html.index("<details>") < html.index('action="/token"')


def test_con_la_contrasena_rechazada_se_pide_la_nueva(cliente_autenticado, usuario_de_prueba, sesion_db):
    """A19: no dice "conectada" si LaLiga ya no acepta lo guardado."""
    from fantasy.auth.credenciales_store import (
        ERROR_CREDENCIALES_RECHAZADAS,
        _buscar,
        guardar_credenciales,
    )

    usuario, _ = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, "laliga@example.com", "la-vieja")
    assert "está conectada" in cliente_autenticado.get("/token").text

    _buscar(sesion_db, usuario.id).ultimo_error = ERROR_CREDENCIALES_RECHAZADAS
    sesion_db.commit()
    html = cliente_autenticado.get("/token").text

    assert "Tu contraseña de LaLiga ha cambiado" in html
    assert 'action="/credenciales"' in html
    assert "está conectada" not in html
