"""Login automático contra LaLiga (decisión revisada, 2026-08-28).

Ver design.md §Login automático: el usuario aceptó guardar credenciales cifradas para que
el token se renueve solo y el cron del paso 9 sea viable.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
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


# --- login por la página web de B2C (ROPC retirado por LaLiga el 13/10/2026) -----------

TENANT = "/laligadspprob2c.onmicrosoft.com/B2C_1A_5ULAIP_PARAMETRIZED_SignIn"
PAGINA_LOGIN = (
    'var SETTINGS = {"csrf": "CSRF-1", "transId": "StateProperties=TX1", '
    f'"api": "CombinedSigninAndSignup", "tenant": "{TENANT}"}};'
)


class _LaLigaFalsa:
    """Las cuatro peticiones del recorrido, con la respuesta de cada paso configurable."""

    def __init__(self, *, formulario=None, destino=None, canje=None, pagina=PAGINA_LOGIN):
        self.pagina = pagina
        self.formulario = formulario or {"status": "200"}
        self.destino = destino  # None: la vuelta buena, con el state de la transacción
        self.canje = canje or httpx.Response(200, json={"id_token": "jwt-nuevo", "refresh_token": "r1"})
        self.enviado: dict = {}
        self.cabeceras: dict = {}
        self.state = None

    def get(self, cliente, url, params=None, **_kw):
        if "/authorize" in url:
            self.state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
            return httpx.Response(200, text=self.pagina, request=httpx.Request("GET", url))
        assert url.endswith(f"{TENANT}/api/CombinedSigninAndSignup/confirmed")
        assert params["csrf_token"] == "CSRF-1" and params["tx"] == "StateProperties=TX1"
        destino = self.destino or f"https://jwt.ms/#state={self.state}&code=COD-1"
        return httpx.Response(302, headers={"location": destino})

    def post(self, cliente, url, data=None, params=None, headers=None, **_kw):
        if url.endswith(f"{TENANT}/SelfAsserted"):
            self.enviado, self.cabeceras = data, headers
            return httpx.Response(200, json=self.formulario)
        assert "oauth2/v2.0/token" in url and data["grant_type"] == "authorization_code"
        return self.canje

    def __enter__(self):
        self._parches = [patch("httpx.Client.get", lambda c, u, **kw: self.get(c, u, **kw)),
                         patch("httpx.Client.post", lambda c, u, **kw: self.post(c, u, **kw))]
        for p in self._parches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._parches:
            p.stop()


def test_login_web_rellena_el_formulario_y_canjea_el_codigo():
    with _LaLigaFalsa() as laliga:
        tokens = laliga_login.iniciar_sesion(EMAIL, PASSWORD)

    assert tokens.access_token == "jwt-nuevo" and tokens.refresh_token == "r1"
    assert laliga.enviado == {"request_type": "RESPONSE", "signInName": EMAIL, "password_login": PASSWORD}
    assert laliga.cabeceras["X-CSRF-TOKEN"] == "CSRF-1"


@pytest.mark.parametrize("codigo", ["AADB2C90053", "AADB2C90054"])
def test_login_web_credenciales_malas(codigo):
    with _LaLigaFalsa(formulario={"status": "400", "errorCode": codigo, "message": "MSG"}):
        with pytest.raises(laliga_login.CredencialesInvalidas):
            laliga_login.iniciar_sesion(EMAIL, PASSWORD)


@pytest.mark.parametrize(
    "laliga",
    [
        # El formulario cambió: no es culpa del usuario, no se le piden las credenciales.
        dict(formulario={"status": "400", "errorCode": "AADB2C90000", "message": "Missing required element"}),
        dict(pagina="<html>otra página</html>"),
        dict(destino="https://login.laliga.es/otra/pantalla"),
        dict(destino="https://jwt.ms/#error=server_error&error_description=x"),
        dict(destino="https://jwt.ms/#state=de-otra-transaccion&code=COD-1"),
        # Un 400 en el canje es del código, no de la contraseña.
        dict(canje=httpx.Response(400, json={"error": "invalid_grant"})),
    ],
    ids=["formulario_cambiado", "pagina_cambiada", "pantalla_nueva", "error_en_la_vuelta",
         "state_ajeno", "canje_rechazado"],
)
def test_login_web_fallos_que_no_son_del_usuario(laliga):
    with _LaLigaFalsa(**laliga):
        with pytest.raises(laliga_login.LoginNoDisponible) as exc:
            laliga_login.iniciar_sesion(EMAIL, PASSWORD)
    assert not isinstance(exc.value, laliga_login.CredencialesInvalidas)


def test_login_no_filtra_la_password_en_el_error():
    """Un mensaje de error no puede arrastrar la contraseña a un log."""
    with patch("httpx.Client.get", side_effect=httpx.ConnectError(f"boom {PASSWORD}")):
        with pytest.raises(laliga_login.LoginNoDisponible) as exc:
            laliga_login.iniciar_sesion(EMAIL, PASSWORD)
    assert PASSWORD not in str(exc.value)

    rechazo = {"status": "400", "errorCode": "AADB2C90000", "message": f"eco {PASSWORD}"}
    with _LaLigaFalsa(formulario=rechazo), pytest.raises(laliga_login.LoginNoDisponible) as exc:
        laliga_login.iniciar_sesion(EMAIL, PASSWORD)
    assert PASSWORD not in str(exc.value)


def test_refrescar_usa_el_scope_del_flujo_de_codigo():
    """Los tokens de `iniciar_sesion` salen ya del flujo de código: se renuevan con su scope."""
    llamadas = []

    def _post(cliente, url, data=None, **_kw):
        llamadas.append(data)
        return httpx.Response(200, json={"id_token": "jwt", "refresh_token": "r2"})

    with patch("httpx.Client.post", _post):
        laliga_login.refrescar("r1")
    assert llamadas[0]["scope"] == "openid offline_access"
