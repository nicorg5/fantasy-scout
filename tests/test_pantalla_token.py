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
