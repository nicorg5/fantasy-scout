"""R11: el token se guarda cifrado, con expiración, y nunca en claro en la fila."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fantasy.auth.token_store import (
    expiracion_del_jwt,
    guardar_token,
    leer_token_descifrado,
)


def test_expiracion_se_lee_del_jwt(sesion_db, usuario_de_prueba, jwt_falso):
    """El `exp` del token manda sobre el respaldo de 24h: si el usuario pega un token
    ya casi caducado, no debe darse por válido 24h más."""
    usuario, _ = usuario_de_prueba
    exp = datetime.now(timezone.utc) + timedelta(hours=3)
    fila = guardar_token(sesion_db, usuario.id, jwt_falso(exp))

    assert abs((fila.expira_en - exp).total_seconds()) < 2


def test_token_no_jwt_usa_el_respaldo(sesion_db, usuario_de_prueba):
    usuario, _ = usuario_de_prueba
    antes = datetime.now(timezone.utc)
    fila = guardar_token(sesion_db, usuario.id, "esto-no-es-un-jwt")

    assert fila.expira_en > antes + timedelta(hours=23)


def test_expiracion_del_jwt_devuelve_none_si_no_es_jwt():
    assert expiracion_del_jwt("cualquier-cosa") is None
    assert expiracion_del_jwt("a.b.c") is None


def test_token_se_guarda_cifrado_y_con_expiracion(sesion_db, usuario_de_prueba):
    usuario, _password = usuario_de_prueba
    token_en_claro = "bearer-de-prueba-super-secreto"

    fila = guardar_token(sesion_db, usuario.id, token_en_claro)

    assert token_en_claro.encode() not in fila.token_cifrado
    assert fila.expira_en is not None

    assert leer_token_descifrado(sesion_db, usuario.id) == token_en_claro
