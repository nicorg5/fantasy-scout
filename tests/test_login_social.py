"""Login social contra LaLiga: código de autorización + PKCE vía jwt.ms (specs/registro-social, T2).

Todo el HTTP está parcheado: lo verificado contra LaLiga de verdad está en
specs/registro/tasks.md §Investigación.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from unittest.mock import patch

import httpx
import pytest

from fantasy.auth import laliga_login
from fantasy.auth.laliga_login import ResultadoSondeo


def _jwt(claims: dict) -> str:
    carga = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"cabecera.{carga}.firma"


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


def test_el_enlace_lleva_exactamente_lo_verificado():
    verificador = "v" * 64
    params = _params(laliga_login.url_login_social("estado123", verificador))

    assert params["p"] == "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
    assert params["redirect_uri"] == "https://jwt.ms"
    assert params["response_mode"] == "fragment"
    assert params["response_type"] == "code"
    assert params["scope"] == "openid offline_access"
    assert params["state"] == params["nonce"] == "estado123"
    assert params["code_challenge_method"] == "S256"
    esperado = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == esperado
    assert verificador not in laliga_login.url_login_social("estado123", verificador)


@pytest.mark.parametrize(
    "pegado",
    [
        "https://jwt.ms/#state=abc&code=COD-123",
        "  https://jwt.ms/#state=abc&code=COD-123\n",
        "state=abc&code=COD-123",
        "#state=abc&code=COD-123",
        "https://jwt.ms/?state=abc&code=COD-123",
    ],
)
def test_extraer_codigo_acepta_la_direccion_o_solo_su_final(pegado):
    assert laliga_login.extraer_codigo(pegado) == ("abc", "COD-123")


def test_extraer_codigo_con_error_de_laliga():
    with pytest.raises(laliga_login.ErrorEnLaDireccion, match="cancel"):
        laliga_login.extraer_codigo("https://jwt.ms/#error=access_denied&error_description=user+cancelled")


@pytest.mark.parametrize("pegado", ["", "hola", "https://jwt.ms/", "https://jwt.ms/#state=abc"])
def test_extraer_codigo_sin_codigo(pegado):
    with pytest.raises(laliga_login.DireccionSinCodigo):
        laliga_login.extraer_codigo(pegado)


def _post_que_responde(codigo: int, cuerpo: dict | None = None):
    llamadas = []

    def _post(self, url, data=None, headers=None):
        llamadas.append((url, data))
        return httpx.Response(codigo, json=cuerpo or {})

    return _post, llamadas


def test_canjear_codigo_devuelve_tokens_email_y_oid():
    id_token = _jwt({"email": "amigo@gmail.com", "oid": "oid-1", "idp": "google.com", "exp": 2_000_000_000})
    post, llamadas = _post_que_responde(200, {"id_token": id_token, "refresh_token": "r1", "id_token_expires_in": 86400})

    with patch("httpx.Client.post", post):
        social = laliga_login.canjear_codigo("COD", "verificador")

    assert social.email == "amigo@gmail.com" and social.oid == "oid-1"
    assert social.tokens.access_token == id_token and social.tokens.refresh_token == "r1"
    url, datos = llamadas[0]
    assert "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN" in url
    assert datos["grant_type"] == "authorization_code"
    assert datos["code_verifier"] == "verificador" and datos["redirect_uri"] == "https://jwt.ms"


def test_canjear_codigo_con_emails_en_lista_y_sin_email():
    con_lista, _ = _post_que_responde(200, {"id_token": _jwt({"emails": ["a@b.c"], "sub": "s1"})})
    with patch("httpx.Client.post", con_lista):
        social = laliga_login.canjear_codigo("COD", "v")
    assert (social.email, social.oid) == ("a@b.c", "s1")

    sin_email, _ = _post_que_responde(200, {"id_token": _jwt({"oid": "o"})})
    with patch("httpx.Client.post", sin_email):
        assert laliga_login.canjear_codigo("COD", "v").email is None


def test_canjear_codigo_caducado_y_servicio_caido():
    rechazo, _ = _post_que_responde(400, {"error": "invalid_grant"})
    with patch("httpx.Client.post", rechazo), pytest.raises(laliga_login.CredencialesInvalidas):
        laliga_login.canjear_codigo("COD", "v")

    with patch("httpx.Client.post", side_effect=httpx.ConnectError("boom")), pytest.raises(laliga_login.LoginNoDisponible):
        laliga_login.canjear_codigo("COD", "v")


def test_refrescar_social_usa_el_scope_verificado():
    post, llamadas = _post_que_responde(200, {"id_token": _jwt({}), "refresh_token": "r2"})
    with patch("httpx.Client.post", post):
        tokens = laliga_login.refrescar_social("r1")
    assert tokens.refresh_token == "r2"
    assert llamadas[0][1]["scope"] == "openid offline_access"
    assert llamadas[0][1]["refresh_token"] == "r1"


# --- sondeo de jwt.ms (S17) -------------------------------------------------------------


def _get_secuencia(*respuestas):
    cola = list(respuestas)

    def _get(self, url, **_kwargs):
        siguiente = cola.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    return _get


PAGINA_LOGIN = httpx.Response(200, text='<html>"GoogleExchange" "AppleExchange"</html>')
NO_REGISTRADA = httpx.Response(302, headers={"location": "https://jwt.ms/#error=redirect_uri_mismatch&error_description=AADB2C90006%3a+The+redirect+URI"})


@pytest.mark.parametrize(
    ("respuestas", "esperado"),
    [
        ((PAGINA_LOGIN,), ResultadoSondeo.ACEPTADA),
        ((NO_REGISTRADA,), ResultadoSondeo.RECHAZADA),
        ((httpx.ConnectError("x"), PAGINA_LOGIN), ResultadoSondeo.ACEPTADA),  # reintento
        ((httpx.ConnectError("x"), httpx.ConnectError("x")), ResultadoSondeo.NO_COMPROBADA),
        ((httpx.Response(500), httpx.Response(500)), ResultadoSondeo.NO_COMPROBADA),
    ],
    ids=["aceptada", "rechazada", "reintento_ok", "sin_red", "respuesta_rara"],
)
def test_sondear_vuelta_social(respuestas, esperado):
    with patch("httpx.Client.get", _get_secuencia(*respuestas)):
        assert laliga_login.sondear_vuelta_social(espera_reintento=0).resultado is esperado
