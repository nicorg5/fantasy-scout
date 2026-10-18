"""Experimento: login con Google en LaLiga por código de autorización + PKCE, vía jwt.ms.

    uv run python specs/registro/spike_google.py enlace                          # 1. enlace
    uv run python specs/registro/spike_google.py canjear 'https://jwt.ms/#code=...'   # 2. canje

`https://jwt.ms` es una página pública de Microsoft que el cliente de LaLiga tiene registrada
como dirección de vuelta: en un iPhone el código queda en la barra de direcciones. Con
`response_mode=fragment` el código va tras "#", que el navegador nunca envía a jwt.ms.

Solo muestra datos NO sensibles: nunca imprime tokens, y el email sale enmascarado. Los
tokens viven en memoria y se descartan al terminar. El único fichero que se escribe es el
verificador PKCE, que sin el código no sirve de nada, y se borra al canjear.

Vive en specs/ (fuera de git) a propósito: es un experimento, no código de la app.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import httpx

TENANT = "https://login.laliga.es/laligadspprob2c.onmicrosoft.com/oauth2/v2.0"
POLITICA = "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"
REDIRECT = "https://jwt.ms"
SCOPE = "openid offline_access"
API = "https://fantasy-api.llt-services.com/api/v1/competition/1/leagues"
VERIFICADOR = Path(__file__).with_name(".pkce_verifier")


def enlace() -> None:
    verificador = secrets.token_urlsafe(64)
    VERIFICADOR.write_text(verificador)
    VERIFICADOR.chmod(0o600)
    estado = secrets.token_urlsafe(16)
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")
    q = urllib.parse.urlencode({
        "p": POLITICA, "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
        "response_type": "code", "response_mode": "fragment", "scope": SCOPE,
        "code_challenge": reto, "code_challenge_method": "S256",
        "state": estado, "nonce": estado,  # B2C exige nonce
    })
    print(f"{TENANT}/authorize?{q}")


def _claims(jwt: str) -> dict:
    carga = jwt.split(".")[1]
    carga += "=" * (-len(carga) % 4)
    return json.loads(base64.urlsafe_b64decode(carga))


def _mascara(email: str) -> str:
    usuario, _, dominio = email.partition("@")
    return f"{usuario[:1]}***@{dominio}"


def _resumen(nombre: str, respuesta: dict) -> str:
    print(f"\n== {nombre} ==")
    print("  campos de la respuesta:", sorted(respuesta))
    for clave in ("expires_in", "id_token_expires_in", "refresh_token_expires_in", "not_before"):
        if clave in respuesta:
            print(f"  {clave}: {respuesta[clave]}")
    token = respuesta.get("id_token") or respuesta.get("access_token")
    claims = _claims(token)
    print("  claims del token:", sorted(claims))
    for clave in ("idp", "tfp", "acr", "iss", "aud"):
        if clave in claims:
            print(f"  {clave}: {claims[clave]}")
    emails = claims.get("emails") or ([claims["email"]] if "email" in claims else [])
    print("  email en el token:", [_mascara(e) for e in emails] or "NO")
    print("  caduca:", datetime.fromtimestamp(claims["exp"], tz=timezone.utc).isoformat())
    print("  trae refresh_token:", "refresh_token" in respuesta)
    return token


def _probar_api(token: str) -> None:
    r = httpx.get(API, params={"x-lang": "es"}, headers={"Authorization": f"Bearer {token}", "x-app": "2"}, timeout=20)
    ligas = r.json() if r.status_code == 200 else None
    n = len(ligas if isinstance(ligas, list) else (ligas or {}).get("data", [])) if ligas is not None else "-"
    print(f"  API de LaLiga con este token: HTTP {r.status_code}, ligas: {n}")


def canjear(url_redirect: str) -> None:
    partes = urllib.parse.urlsplit(url_redirect.strip())
    params = urllib.parse.parse_qs(partes.fragment or partes.query)
    if "error" in params:
        sys.exit(f"LaLiga devolvió un error: {params.get('error')} {params.get('error_description')}")
    if "code" not in params:
        sys.exit(f"La dirección no trae 'code'. Parámetros: {sorted(params)}")
    if not VERIFICADOR.exists():
        sys.exit("No está el verificador: genera un enlace nuevo con 'enlace' y repite el login.")
    datos = {
        "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": params["code"][0],
        "code_verifier": VERIFICADOR.read_text(), "redirect_uri": REDIRECT, "scope": SCOPE,
    }
    r = httpx.post(f"{TENANT}/token", params={"p": POLITICA}, data=datos, timeout=20)
    VERIFICADOR.unlink(missing_ok=True)
    if r.status_code != 200:
        sys.exit(f"Canje fallido: HTTP {r.status_code} {r.text[:300]}")
    primera = r.json()
    _probar_api(_resumen("canje del código", primera))

    r = httpx.post(f"{TENANT}/token", params={"p": POLITICA}, data={
        "grant_type": "refresh_token", "client_id": CLIENT_ID, "scope": SCOPE,
        "refresh_token": primera["refresh_token"],
    }, timeout=20)
    if r.status_code != 200:
        sys.exit(f"Renovación fallida: HTTP {r.status_code} {r.text[:300]}")
    segunda = r.json()
    _probar_api(_resumen("renovación con refresh token", segunda))
    print("\n  el refresh token rota:", segunda.get("refresh_token") != primera.get("refresh_token"))


if __name__ == "__main__":
    if sys.argv[1:2] == ["enlace"]:
        enlace()
    elif sys.argv[1:2] == ["canjear"] and len(sys.argv) == 3:
        canjear(sys.argv[2])
    else:
        sys.exit(__doc__)
