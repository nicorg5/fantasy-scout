"""Login automático contra LaLiga (decisión revisada, 2026-08-28).

Ver design.md §Login automático: el usuario aceptó guardar credenciales cifradas para que
el token se renueve solo y el cron del paso 9 sea viable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from fantasy.auth import laliga_login
from fantasy.auth.credenciales_store import (
    borrar_credenciales,
    guardar_credenciales,
    obtener_token_valido,
    tiene_credenciales,
)
from fantasy.auth.token_store import guardar_token

EMAIL = "yo@example.com"
PASSWORD = "mi-password-de-laliga"


def _tokens(access: str = "token-nuevo", refresh: str | None = "refresh-abc"):
    return laliga_login.TokensLaLiga(
        access_token=access,
        refresh_token=refresh,
        expira_en=datetime.now(timezone.utc) + timedelta(hours=24),
    )


def test_credenciales_se_guardan_cifradas(sesion_db, usuario_de_prueba):
    usuario, _ = usuario_de_prueba

    fila = guardar_credenciales(sesion_db, usuario.id, EMAIL, PASSWORD)

    assert PASSWORD.encode() not in fila.password_cifrado
    assert EMAIL.encode() not in fila.email_cifrado
    assert tiene_credenciales(sesion_db, usuario.id)


def test_token_valido_existente_no_dispara_login(sesion_db, usuario_de_prueba, jwt_falso):
    """Lo más barato primero: si el token sirve, no se molesta a LaLiga."""
    usuario, _ = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, EMAIL, PASSWORD)
    vigente = jwt_falso(datetime.now(timezone.utc) + timedelta(hours=5))
    guardar_token(sesion_db, usuario.id, vigente)

    with patch.object(laliga_login, "iniciar_sesion") as login:
        with patch.object(laliga_login, "refrescar") as refresh:
            token = obtener_token_valido(sesion_db, usuario.id)

    assert token == vigente
    login.assert_not_called()
    refresh.assert_not_called()


def test_sin_token_hace_login_y_lo_guarda(sesion_db, usuario_de_prueba):
    usuario, _ = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, EMAIL, PASSWORD)

    with patch("fantasy.auth.credenciales_store.laliga_login.iniciar_sesion",
               return_value=_tokens()) as login:
        token = obtener_token_valido(sesion_db, usuario.id)

    assert token == "token-nuevo"
    login.assert_called_once_with(EMAIL, PASSWORD)
    # Y queda guardado para la próxima.
    with patch("fantasy.auth.credenciales_store.laliga_login.iniciar_sesion") as login2:
        assert obtener_token_valido(sesion_db, usuario.id) == "token-nuevo"
        login2.assert_not_called()


def test_se_prefiere_refresh_sobre_login_completo(sesion_db, usuario_de_prueba):
    """El refresh no necesita credenciales: es la vía menos expuesta."""
    usuario, _ = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, EMAIL, PASSWORD)

    with patch("fantasy.auth.credenciales_store.laliga_login.iniciar_sesion",
               return_value=_tokens("primero", "refresh-1")):
        obtener_token_valido(sesion_db, usuario.id)

    # Caduca el token, pero seguimos teniendo refresh_token.
    from fantasy.storage.modelos import TokenUsuario
    fila = sesion_db.query(TokenUsuario).filter_by(user_id=usuario.id).one()
    fila.expira_en = datetime.now(timezone.utc) - timedelta(minutes=1)
    sesion_db.commit()

    with patch("fantasy.auth.credenciales_store.laliga_login.refrescar",
               return_value=_tokens("por-refresh", "refresh-2")) as refrescar:
        with patch("fantasy.auth.credenciales_store.laliga_login.iniciar_sesion") as login:
            token = obtener_token_valido(sesion_db, usuario.id)

    assert token == "por-refresh"
    refrescar.assert_called_once()
    login.assert_not_called()


def test_credenciales_rechazadas_no_se_reintentan(sesion_db, usuario_de_prueba):
    """Repetir con credenciales malas no arregla nada y machaca el login de LaLiga."""
    usuario, _ = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, EMAIL, PASSWORD)

    with patch("fantasy.auth.credenciales_store.laliga_login.iniciar_sesion",
               side_effect=laliga_login.CredencialesInvalidas("no")) as login:
        assert obtener_token_valido(sesion_db, usuario.id) is None
        assert login.call_count == 1

    from fantasy.storage.modelos import CredencialesLaLiga
    fila = sesion_db.query(CredencialesLaLiga).filter_by(user_id=usuario.id).one()
    assert fila.ultimo_error, "el fallo debe quedar registrado para poder avisar al usuario"


def test_sin_credenciales_no_hay_token_automatico(sesion_db, usuario_de_prueba):
    """Guardar credenciales es opcional: sin ellas, solo queda la vía manual."""
    usuario, _ = usuario_de_prueba
    assert obtener_token_valido(sesion_db, usuario.id) is None


def test_borrar_credenciales(sesion_db, usuario_de_prueba):
    usuario, _ = usuario_de_prueba
    guardar_credenciales(sesion_db, usuario.id, EMAIL, PASSWORD)

    assert borrar_credenciales(sesion_db, usuario.id) is True
    assert not tiene_credenciales(sesion_db, usuario.id)


def test_login_no_filtra_la_password_en_el_error():
    """Un mensaje de error no puede arrastrar la contraseña a un log."""
    import httpx

    with patch("httpx.Client.post", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(laliga_login.LoginNoDisponible) as exc:
            laliga_login.iniciar_sesion(EMAIL, PASSWORD)

    assert PASSWORD not in str(exc.value)
