"""Alta de cuentas de LaLiga creadas con Google u otra red social (specs/registro-social)."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import logging
import re
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, inspect, select, text

from fantasy.auth import alta, intentos, invitaciones, laliga_login
from fantasy.auth.credenciales_store import (
    ERROR_RECONEXION,
    EstadoConexion,
    _buscar,
    _fernet,
    escribir_credenciales_social,
    escribir_tokens,
    estado_conexion,
    guardar_credenciales,
    obtener_token_valido,
)
from fantasy.auth.invitaciones import EstadoInvitacion
from fantasy.auth.passwords import verificar_password
from fantasy.auth.sesiones import NOMBRE_COOKIE, leer_usuario_id_de_cookie
from fantasy.auth.token_store import leer_token_descifrado
from fantasy.observabilidad import consultas, vigilancia
from fantasy.official.errores import ConexionCaducada
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import (
    CredencialesLaLiga,
    EventoUso,
    IntentoConexion,
    Invitacion,
    TipoCredenciales,
    Usuario,
)

_MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/4370e778ca9a_cuentas_sociales.py"


def _cargar_migracion():
    spec = importlib.util.spec_from_file_location("migracion_sociales", _MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# --------------------------------------------------------------------------------------
# T1: esquema (D1, D2, D9)


def test_esquema_de_credenciales_e_intentos(sesion_db):
    inspector = inspect(sesion_db.get_bind())
    credenciales = {c["name"]: c for c in inspector.get_columns("laliga_credentials")}
    assert credenciales["password_cifrado"]["nullable"] is True
    assert credenciales["tipo"]["nullable"] is False
    assert "laliga_oid" in credenciales

    intentos = {c["name"] for c in inspector.get_columns("intento_conexion")}
    assert intentos == {"id", "state", "verificador_cifrado", "invitacion_id", "usuario_id", "caduca_en"}
    assert all(fk["options"]["ondelete"] == "CASCADE" for fk in inspector.get_foreign_keys("intento_conexion"))


def test_las_credenciales_de_siempre_son_de_tipo_password(sesion_db, usuario_de_prueba):
    usuario, _ = usuario_de_prueba
    fila = guardar_credenciales(sesion_db, usuario.id, "laliga@example.com", "x" * 10)
    assert fila.tipo == TipoCredenciales.PASSWORD.value


def test_el_downgrade_se_niega_si_hay_cuentas_sociales(usuario_de_prueba):
    """D9: todo dentro de una transacción que se deshace."""
    usuario, _ = usuario_de_prueba
    with obtener_fabrica_sesiones()() as sesion:
        conexion = sesion.connection()
        migracion = _cargar_migracion()
        migracion.comprobar_sin_cuentas_sociales(conexion)  # sin sociales: no dice nada
        conexion.execute(
            text("INSERT INTO laliga_credentials (id, user_id, tipo, email_cifrado) "
                 "VALUES (gen_random_uuid(), :u, 'social', 'x')"),
            {"u": usuario.id},
        )
        with pytest.raises(RuntimeError, match="social"):
            migracion.comprobar_sin_cuentas_sociales(conexion)
        sesion.rollback()
    with obtener_fabrica_sesiones()() as sesion:
        assert sesion.query(CredencialesLaLiga).filter_by(user_id=usuario.id).count() == 0


# --------------------------------------------------------------------------------------
# T3: intentos de conexión (S3)


@pytest.fixture
def invitacion(sesion_db):
    fila, token = invitaciones.crear(sesion_db)
    yield fila, token
    sesion_db.execute(delete(Invitacion).where(Invitacion.id == fila.id))
    sesion_db.commit()


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


def test_el_intento_guarda_el_verificador_del_enlace_cifrado(sesion_db, invitacion):
    fila, _ = invitacion
    intento, url = intentos.crear(sesion_db, invitacion_id=fila.id)
    verificador = intentos.verificador(intento)
    params = _params(url)

    assert params["state"] == intento.state
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == reto
    assert verificador.encode() not in intento.verificador_cifrado
    assert timedelta(minutes=14) < intento.caduca_en - datetime.now(timezone.utc) <= timedelta(minutes=15)


def test_un_segundo_intento_sustituye_al_primero(sesion_db, invitacion):
    fila, _ = invitacion
    primero, _ = intentos.crear(sesion_db, invitacion_id=fila.id)
    estado_primero = primero.state
    segundo, _ = intentos.crear(sesion_db, invitacion_id=fila.id)

    sesion_db.expire_all()
    assert intentos.buscar_vigente(sesion_db, estado_primero) is None
    assert intentos.buscar_vigente(sesion_db, segundo.state).id == segundo.id


def test_un_intento_caducado_no_se_encuentra_y_se_limpia(sesion_db, invitacion, usuario_de_prueba):
    fila, _ = invitacion
    viejo, _ = intentos.crear(sesion_db, invitacion_id=fila.id, ahora=datetime.now(timezone.utc) - timedelta(minutes=20))
    estado_viejo = viejo.state
    assert intentos.buscar_vigente(sesion_db, estado_viejo) is None

    # Crear otro intento, aunque sea de otro dueño, limpia los caducados.
    intentos.crear(sesion_db, usuario_id=usuario_de_prueba[0].id)
    sesion_db.expire_all()
    assert sesion_db.scalar(select(IntentoConexion).where(IntentoConexion.state == estado_viejo)) is None


def test_consumir_borra_el_intento(sesion_db, usuario_de_prueba):
    intento, _ = intentos.crear(sesion_db, usuario_id=usuario_de_prueba[0].id)
    estado = intento.state
    assert intentos.consumir(sesion_db, intento)
    sesion_db.commit()
    assert intentos.buscar_vigente(sesion_db, estado) is None


def test_un_intento_es_de_una_invitacion_o_de_un_usuario():
    with pytest.raises(ValueError):
        intentos.crear(None)
    with pytest.raises(ValueError):
        intentos.crear(None, invitacion_id=uuid.uuid4(), usuario_id=uuid.uuid4())


# --------------------------------------------------------------------------------------
# T4: renovación de una cuenta social, aviso y login (S8, S9, S13)


def _tokens(jwt_falso, refresh: str | None = "refresh-1", horas: int = 24) -> laliga_login.TokensLaLiga:
    exp = datetime.now(timezone.utc) + timedelta(hours=horas)
    return laliga_login.TokensLaLiga(jwt_falso(exp), refresh, exp)


@pytest.fixture
def cuenta_social(sesion_db, usuario_de_prueba, jwt_falso):
    """El usuario de prueba convertido en cuenta social, con su token ya CADUCADO para que
    cualquier petición tenga que renovar."""
    usuario, password = usuario_de_prueba
    fila = escribir_credenciales_social(sesion_db, usuario.id, "amigo@gmail.com", "oid-amigo")
    escribir_tokens(sesion_db, usuario.id, _tokens(jwt_falso, "refresh-1", horas=-1), fila)
    sesion_db.commit()
    return usuario, password


def test_cuenta_social_renueva_solo_con_refresh_y_guarda_el_nuevo(sesion_db, cuenta_social, jwt_falso):
    """S8: el refresh rota y nunca se intenta ROPC."""
    usuario, _ = cuenta_social
    nuevos = _tokens(jwt_falso, "refresh-2")
    with patch.object(laliga_login, "refrescar_social", return_value=nuevos) as refresco, \
         patch.object(laliga_login, "iniciar_sesion") as ropc, \
         patch.object(laliga_login, "refrescar") as refresco_ropc:
        token = obtener_token_valido(sesion_db, usuario.id)

    assert token == nuevos.access_token
    refresco.assert_called_once_with("refresh-1")
    ropc.assert_not_called()
    refresco_ropc.assert_not_called()
    sesion_db.expire_all()
    fila = _buscar(sesion_db, usuario.id)
    assert _fernet().decrypt(fila.refresh_token_cifrado).decode() == "refresh-2"
    assert estado_conexion(sesion_db, usuario.id) is EstadoConexion.CONECTADA


def test_si_laliga_ya_no_renueva_hay_que_reconectar(sesion_db, cuenta_social):
    """S9: 400 al renovar → reconectar, sin probar nada más."""
    usuario, _ = cuenta_social
    with patch.object(laliga_login, "refrescar_social", side_effect=laliga_login.CredencialesInvalidas("x")), \
         patch.object(laliga_login, "iniciar_sesion") as ropc:
        assert obtener_token_valido(sesion_db, usuario.id) is None
    ropc.assert_not_called()
    assert estado_conexion(sesion_db, usuario.id) is EstadoConexion.RECONECTAR
    assert _buscar(sesion_db, usuario.id).ultimo_error == ERROR_RECONEXION


def test_si_el_login_de_laliga_esta_caido_no_hay_que_reconectar(sesion_db, cuenta_social):
    """S9: un fallo pasajero no pide al usuario que haga nada."""
    usuario, _ = cuenta_social
    with patch.object(laliga_login, "refrescar_social", side_effect=laliga_login.LoginNoDisponible("x")):
        assert obtener_token_valido(sesion_db, usuario.id) is None
    assert estado_conexion(sesion_db, usuario.id) is EstadoConexion.CONECTADA


def test_el_cliente_lanza_conexion_caducada(sesion_db, cuenta_social):
    from fantasy.official.cliente import ClienteOficial

    usuario, _ = cuenta_social
    with patch.object(laliga_login, "refrescar_social", side_effect=laliga_login.CredencialesInvalidas("x")):
        with pytest.raises(ConexionCaducada):
            ClienteOficial(sesion_db, usuario.id).obtener_mercado("012345678")


@pytest.mark.parametrize(
    ("ruta", "destino"),
    [
        ("/plantilla", "obtener_plantilla"),
        ("/plantilla/tabla", "obtener_plantilla"),
        ("/clausulas", "obtener_clausulas"),
    ],
)
def test_conexion_caducada_avisa_con_200(cliente_autenticado, ruta, destino):
    """S9"""
    with patch(f"fantasy.api.app.{destino}", side_effect=ConexionCaducada("x")):
        respuesta = cliente_autenticado.get(ruta)
    assert respuesta.status_code == 200
    assert "Tu conexión con LaLiga ha caducado" in respuesta.text
    assert 'href="/token"' in respuesta.text


def test_una_cuenta_social_entra_con_su_contrasena_sin_llamar_a_laliga(cliente, cuenta_social):
    """S13"""
    usuario, password = cuenta_social
    with patch.object(laliga_login, "iniciar_sesion") as ropc:
        mala = cliente.post("/login", data={"email": usuario.email, "password": "otra-cosa-123"}, follow_redirects=False)
        buena = cliente.post("/login", data={"email": usuario.email, "password": password}, follow_redirects=False)
    assert mala.status_code == 401
    assert buena.status_code == 302
    ropc.assert_not_called()


# --------------------------------------------------------------------------------------
# T5: alta social (S4-S6)

PROPIA = "mi-contrasena-propia"


@pytest.fixture
def email_social(sesion_db):
    email = f"social-{uuid.uuid4().hex[:8]}@gmail.com"
    yield email
    sesion_db.execute(delete(Usuario).where(Usuario.email == email))
    sesion_db.commit()


@pytest.fixture
def laliga_canjea(monkeypatch, jwt_falso, email_social):
    """Canje falso: devuelve tokens con el email de `email_social` (en mayúsculas, para
    comprobar que se normaliza) salvo que se le diga otra cosa."""
    estado = {"llamadas": [], "lanza": None, "email": email_social.upper(), "oid": "oid-amigo"}

    def _canjear(code, verificador):
        estado["llamadas"].append((code, verificador))
        if estado["lanza"] is not None:
            raise estado["lanza"]
        return laliga_login.TokensSocial(_tokens(jwt_falso, "refresh-1"), estado["email"], estado["oid"])

    monkeypatch.setattr(laliga_login, "canjear_codigo", _canjear)
    return estado


def _pegado(intento) -> str:
    return f"https://jwt.ms/#state={intento.state}&code=COD-1"


def _estado_invitacion(sesion, fila):
    sesion.expire_all()
    return invitaciones.estado(sesion.get(Invitacion, fila.id))


def test_alta_social_correcta(sesion_db, invitacion, email_social, laliga_canjea):
    """S4"""
    fila, token = invitacion
    intento, _ = intentos.crear(sesion_db, invitacion_id=fila.id)
    verificador = intentos.verificador(intento)
    estado_intento = intento.state

    usuario = alta.dar_de_alta_social(sesion_db, token, _pegado(intento), PROPIA, PROPIA)

    assert usuario.email == email_social  # normalizado
    assert verificar_password(PROPIA, usuario.password_hash)
    credenciales = _buscar(sesion_db, usuario.id)
    assert credenciales.tipo == TipoCredenciales.SOCIAL.value
    assert credenciales.laliga_oid == "oid-amigo"
    assert credenciales.password_cifrado is None
    assert _fernet().decrypt(credenciales.refresh_token_cifrado).decode() == "refresh-1"
    assert leer_token_descifrado(sesion_db, usuario.id) is not None
    assert _estado_invitacion(sesion_db, fila) is EstadoInvitacion.USADA
    assert intentos.buscar_vigente(sesion_db, estado_intento) is None
    assert laliga_canjea["llamadas"] == [("COD-1", verificador)]


@pytest.mark.parametrize(
    ("password", "repetida", "texto"),
    [("corta", "corta", "al menos 8"), (PROPIA, PROPIA + "x", "no coinciden")],
)
def test_contrasena_no_valida_no_gasta_el_codigo(sesion_db, invitacion, laliga_canjea, password, repetida, texto):
    fila, token = invitacion
    intento, _ = intentos.crear(sesion_db, invitacion_id=fila.id)
    with pytest.raises(alta.ContrasenaNoValida, match=texto):
        alta.dar_de_alta_social(sesion_db, token, _pegado(intento), password, repetida)
    assert laliga_canjea["llamadas"] == []
    assert _estado_invitacion(sesion_db, fila) is EstadoInvitacion.PENDIENTE


def test_lo_pegado_no_es_la_direccion(sesion_db, invitacion, laliga_canjea):
    _, token = invitacion
    with pytest.raises(alta.DireccionNoValida):
        alta.dar_de_alta_social(sesion_db, token, "hola", PROPIA, PROPIA)
    with pytest.raises(laliga_login.ErrorEnLaDireccion):
        alta.dar_de_alta_social(sesion_db, token, "https://jwt.ms/#error=access_denied", PROPIA, PROPIA)
    assert laliga_canjea["llamadas"] == []


def test_intento_de_otra_invitacion_o_caducado(sesion_db, invitacion, laliga_canjea):
    fila, token = invitacion
    otra, _ = invitaciones.crear(sesion_db)
    try:
        ajeno, _ = intentos.crear(sesion_db, invitacion_id=otra.id)
        with pytest.raises(alta.IntentoNoValido):
            alta.dar_de_alta_social(sesion_db, token, _pegado(ajeno), PROPIA, PROPIA)
        caducado, _ = intentos.crear(sesion_db, invitacion_id=fila.id, ahora=datetime.now(timezone.utc) - timedelta(minutes=16))
        with pytest.raises(alta.IntentoNoValido):
            alta.dar_de_alta_social(sesion_db, token, _pegado(caducado), PROPIA, PROPIA)
    finally:
        sesion_db.execute(delete(Invitacion).where(Invitacion.id == otra.id))
        sesion_db.commit()
    assert laliga_canjea["llamadas"] == []


@pytest.mark.parametrize(
    ("lanza", "excepcion"),
    [
        (laliga_login.CredencialesInvalidas("x"), alta.IntentoNoValido),
        (laliga_login.LoginNoDisponible("x"), laliga_login.LoginNoDisponible),
    ],
    ids=["codigo_caducado", "laliga_caida"],
)
def test_si_el_canje_falla_el_intento_sigue_vivo(sesion_db, invitacion, email_social, laliga_canjea, lanza, excepcion):
    """S5. Y el intento vuelve con el rollback: puede reabrir el mismo enlace de LaLiga."""
    fila, token = invitacion
    intento, _ = intentos.crear(sesion_db, invitacion_id=fila.id)
    estado_intento = intento.state
    laliga_canjea["lanza"] = lanza

    with pytest.raises(excepcion):
        alta.dar_de_alta_social(sesion_db, token, _pegado(intento), PROPIA, PROPIA)

    assert _estado_invitacion(sesion_db, fila) is EstadoInvitacion.PENDIENTE
    assert intentos.buscar_vigente(sesion_db, estado_intento) is not None
    assert sesion_db.scalar(select(Usuario).where(Usuario.email == email_social)) is None


def test_sin_email_en_el_token(sesion_db, invitacion, laliga_canjea):
    fila, token = invitacion
    intento, _ = intentos.crear(sesion_db, invitacion_id=fila.id)
    laliga_canjea["email"] = None
    with pytest.raises(alta.SinEmail):
        alta.dar_de_alta_social(sesion_db, token, _pegado(intento), PROPIA, PROPIA)
    assert _estado_invitacion(sesion_db, fila) is EstadoInvitacion.PENDIENTE


def test_email_que_ya_tiene_cuenta(sesion_db, invitacion, laliga_canjea, usuario_de_prueba):
    fila, token = invitacion
    intento, _ = intentos.crear(sesion_db, invitacion_id=fila.id)
    laliga_canjea["email"] = usuario_de_prueba[0].email.upper()
    with pytest.raises(alta.YaTieneCuenta):
        alta.dar_de_alta_social(sesion_db, token, _pegado(intento), PROPIA, PROPIA)
    assert _estado_invitacion(sesion_db, fila) is EstadoInvitacion.PENDIENTE


def test_si_falla_la_ultima_escritura_no_queda_nada(sesion_db, invitacion, email_social, laliga_canjea, monkeypatch):
    fila, token = invitacion
    intento, _ = intentos.crear(sesion_db, invitacion_id=fila.id)

    def _explota(*_a, **_k):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(invitaciones, "marcar_usada", _explota)
    with pytest.raises(RuntimeError):
        alta.dar_de_alta_social(sesion_db, token, _pegado(intento), PROPIA, PROPIA)
    assert sesion_db.scalar(select(Usuario).where(Usuario.email == email_social)) is None
    assert _estado_invitacion(sesion_db, fila) is EstadoInvitacion.PENDIENTE


def test_dos_altas_simultaneas_mezclando_vias_crean_un_solo_usuario(sesion_db, invitacion, email_social, monkeypatch, jwt_falso):
    """S6, contra Postgres real: una por red social y otra por contraseña de LaLiga, con el
    mismo enlace. LaLiga tarda a propósito para que se solapen."""
    fila, token = invitacion
    intento, _ = intentos.crear(sesion_db, invitacion_id=fila.id)
    pegado = _pegado(intento)
    email_password = f"password-{uuid.uuid4().hex[:8]}@example.com"

    def _lento(resultado):
        def _f(*_args):
            time.sleep(0.5)
            return resultado
        return _f

    monkeypatch.setattr(laliga_login, "canjear_codigo", _lento(laliga_login.TokensSocial(_tokens(jwt_falso), email_social, "oid")))
    monkeypatch.setattr(laliga_login, "iniciar_sesion", _lento(_tokens(jwt_falso)))
    resultados: list[str] = []

    def _social():
        with obtener_fabrica_sesiones()() as sesion:
            try:
                alta.dar_de_alta_social(sesion, token, pegado, PROPIA, PROPIA)
                resultados.append("ok")
            except (alta.EnlaceNoValido, alta.IntentoNoValido):
                resultados.append("rechazada")

    def _password():
        with obtener_fabrica_sesiones()() as sesion:
            try:
                alta.dar_de_alta(sesion, token, email_password, "la-de-laliga")
                resultados.append("ok")
            except alta.EnlaceNoValido:
                resultados.append("rechazada")

    hilos = [threading.Thread(target=_social), threading.Thread(target=_password)]
    try:
        for hilo in hilos:
            hilo.start()
            time.sleep(0.1)
        for hilo in hilos:
            hilo.join(timeout=10)
        assert sorted(resultados) == ["ok", "rechazada"]
        creados = sesion_db.scalars(select(Usuario).where(Usuario.email.in_([email_social, email_password]))).all()
        assert len(creados) == 1
    finally:
        sesion_db.execute(delete(Usuario).where(Usuario.email.in_([email_social, email_password])))
        sesion_db.commit()


# --------------------------------------------------------------------------------------
# T6: pantallas de alta social (S1, S2, S5, S7, S18)

UA = {
    "safari_iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "chrome_iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1",
    "edge_iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/130.0 Mobile/15E148 Safari/605.1.15",
    "chrome_pc": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "edge_pc": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0",
    "safari_mac": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "whatsapp": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 WhatsApp/24.20",
}


def _iniciar(cliente, token, ua=UA["safari_iphone"]):
    return cliente.post("/registro/social/iniciar", data={"invitacion": token}, headers={"User-Agent": ua})


def _enlace_login(html: str) -> str:
    return re.search(r'href="(https://login\.laliga\.es/[^"]+)"', html).group(1).replace("&amp;", "&")


def test_la_invitacion_ofrece_las_dos_vias(cliente, invitacion):
    """S1"""
    _, token = invitacion
    html = cliente.get(f"/registro?invitacion={token}").text
    assert 'action="/registro"' in html
    assert 'action="/registro/social/iniciar"' in html
    assert "Google" in html


def test_iniciar_pinta_los_tres_pasos_con_un_enlace_seguro(cliente, sesion_db, invitacion):
    """S2, S3, D7"""
    fila, token = invitacion
    respuesta = _iniciar(cliente, token)

    assert respuesta.status_code == 200
    html = respuesta.text
    enlace = _enlace_login(html)
    assert "redirect_uri=https%3A%2F%2Fjwt.ms" in enlace
    assert re.search(r'<a href="https://login\.laliga\.es/[^"]+" target="_blank" rel="noopener noreferrer"', html)
    assert 'name="pegado"' in html and 'name="password2"' in html
    assert "aviso-pegar" in html and ".catch(" in html
    state = _params(enlace)["state"]
    assert intentos.buscar_vigente(sesion_db, state).invitacion_id == fila.id


def test_iniciar_con_invitacion_no_valida(cliente):
    respuesta = cliente.post("/registro/social/iniciar", data={"invitacion": "no-existe"})
    assert respuesta.status_code == 400
    assert "ha caducado o ya se ha usado" in respuesta.text


@pytest.mark.parametrize("navegador", ["safari_iphone", "chrome_iphone", "edge_iphone", "chrome_pc", "edge_pc", "safari_mac"])
def test_funciona_igual_en_cada_navegador(cliente, invitacion, navegador):
    """S18: mismo enlace y mismo formulario; solo cambia "toca" / "haz clic"."""
    _, token = invitacion
    html = _iniciar(cliente, token, UA[navegador]).text
    assert "aviso-navegador" not in html
    assert 'name="pegado"' in html
    movil = "iphone" in navegador
    assert ("Toca la barra" in html) is movil
    assert ("Haz clic en la barra" in html) is not movil


def test_avisa_dentro_de_whatsapp_sin_bloquear(cliente, invitacion):
    """D7: aviso, nunca bloqueo."""
    _, token = invitacion
    html = _iniciar(cliente, token, UA["whatsapp"]).text
    assert "aviso-navegador" in html
    assert 'name="pegado"' in html


def _alta_web(cliente, token, pegado, password=PROPIA, password2=PROPIA):
    return cliente.post(
        "/registro/social",
        data={"invitacion": token, "pegado": pegado, "password": password, "password2": password2},
        follow_redirects=False,
    )


def test_alta_social_por_la_web_abre_sesion(cliente, sesion_db, invitacion, email_social, laliga_canjea):
    """S4"""
    fila, token = invitacion
    state = _params(_enlace_login(_iniciar(cliente, token).text))["state"]

    respuesta = _alta_web(cliente, token, f"https://jwt.ms/#state={state}&code=COD-1")

    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/plantilla"
    usuario = sesion_db.scalar(select(Usuario).where(Usuario.email == email_social))
    assert leer_usuario_id_de_cookie(respuesta.cookies[NOMBRE_COOKIE]) == usuario.id


def test_errores_de_la_web(cliente, sesion_db, invitacion, laliga_canjea, usuario_de_prueba):
    """S5: cada error con su código y su texto; el formulario sigue ahí."""
    fila, token = invitacion
    state = _params(_enlace_login(_iniciar(cliente, token).text))["state"]
    bueno = f"https://jwt.ms/#state={state}&code=COD-1"

    casos = [
        (_alta_web(cliente, token, bueno, "corta", "corta"), 400, "al menos 8"),
        (_alta_web(cliente, token, "hola"), 400, "página de Microsoft"),
        (_alta_web(cliente, token, "https://jwt.ms/#error=access_denied&error_description=cancelled"), 400, "no ha completado"),
        (_alta_web(cliente, token, "https://jwt.ms/#state=otro&code=C"), 400, "ha caducado o ya se ha usado. Vuelve al paso 1"),
    ]
    laliga_canjea["lanza"] = laliga_login.LoginNoDisponible("x")
    casos.append((_alta_web(cliente, token, bueno), 503, "No hemos podido contactar"))
    laliga_canjea["lanza"] = None
    laliga_canjea["email"] = None
    casos.append((_alta_web(cliente, token, bueno), 400, "no nos ha dado un email"))

    for respuesta, codigo, texto in casos:
        assert respuesta.status_code == codigo, texto
        assert texto in respuesta.text
        assert 'name="pegado"' in respuesta.text
    assert _estado_invitacion(sesion_db, fila) is EstadoInvitacion.PENDIENTE


def test_si_el_canje_falla_el_paso_1_vuelve_al_mismo_enlace(cliente, invitacion, laliga_canjea):
    _, token = invitacion
    enlace = _enlace_login(_iniciar(cliente, token).text)
    state = _params(enlace)["state"]
    laliga_canjea["lanza"] = laliga_login.LoginNoDisponible("x")

    html = _alta_web(cliente, token, f"https://jwt.ms/#state={state}&code=COD-1").text

    assert _enlace_login(html) == enlace


def test_email_con_cuenta_manda_al_login(cliente, invitacion, laliga_canjea, usuario_de_prueba):
    _, token = invitacion
    state = _params(_enlace_login(_iniciar(cliente, token).text))["state"]
    laliga_canjea["email"] = usuario_de_prueba[0].email
    respuesta = _alta_web(cliente, token, f"https://jwt.ms/#state={state}&code=COD-1")
    assert respuesta.status_code == 409
    assert 'href="/login"' in respuesta.text


def test_nada_secreto_sale_en_logs_ni_en_respuestas(cliente, sesion_db, invitacion, laliga_canjea, caplog):
    """S7: ni verificador, ni código, ni contraseña, ni tokens."""
    caplog.set_level(logging.DEBUG)
    antes = sesion_db.scalar(select(func.max(EventoUso.creado_en)))
    _, token = invitacion
    primera = _iniciar(cliente, token)
    state = _params(_enlace_login(primera.text))["state"]
    verificador = intentos.verificador(intentos.buscar_vigente(sesion_db, state))
    codigo = "CODIGO-SECRETO-123"
    pegado = f"https://jwt.ms/#state={state}&code={codigo}"

    laliga_canjea["lanza"] = laliga_login.LoginNoDisponible("x")
    fallida = _alta_web(cliente, token, pegado)
    laliga_canjea["lanza"] = None
    correcta = _alta_web(cliente, token, pegado)
    assert correcta.status_code == 302

    secretos = [verificador, codigo, PROPIA]
    for respuesta in (primera, fallida, correcta):
        for secreto in secretos:
            assert secreto not in respuesta.text
    for registro in caplog.records:
        for secreto in secretos:
            assert secreto not in registro.getMessage()

    sesion_db.expire_all()
    consulta = select(EventoUso)
    if antes is not None:
        consulta = consulta.where(EventoUso.creado_en > antes)
    for evento in sesion_db.scalars(consulta):
        fila_texto = " ".join(str(v) for v in vars(evento).values())
        for secreto in secretos:
            assert secreto not in fila_texto


# --------------------------------------------------------------------------------------
# T7: reconexión desde Cuenta (S10, S11)


def _marcar_reconectar(sesion, usuario_id):
    _buscar(sesion, usuario_id).ultimo_error = ERROR_RECONEXION
    sesion.commit()


def test_cuenta_social_conectada(cuenta_social, cliente_autenticado):
    html = cliente_autenticado.get("/token").text
    assert "conectada con Google u otra red social" in html
    assert 'action="/credenciales"' not in html  # sin formulario de contraseña de LaLiga
    assert 'action="/token/social/iniciar"' in html


def test_cuenta_social_que_hay_que_reconectar(sesion_db, cuenta_social, cliente_autenticado):
    _marcar_reconectar(sesion_db, cuenta_social[0].id)
    html = cliente_autenticado.get("/token").text
    assert "Tu conexión con LaLiga ha caducado" in html
    assert 'action="/token/social"' in html
    assert 'name="password"' not in html


def _iniciar_reconexion(cliente):
    return _params(_enlace_login(cliente.post("/token/social/iniciar").text))["state"]


def test_reconexion_correcta(sesion_db, cuenta_social, cliente_autenticado, laliga_canjea, jwt_falso):
    """S10: tokens nuevos, sin error; misma cuenta de LaLiga (mismo oid)."""
    usuario, _ = cuenta_social
    _marcar_reconectar(sesion_db, usuario.id)
    state = _iniciar_reconexion(cliente_autenticado)
    assert intentos.buscar_vigente(sesion_db, state).usuario_id == usuario.id
    laliga_canjea["oid"] = "oid-amigo"

    respuesta = cliente_autenticado.post("/token/social", data={"pegado": f"https://jwt.ms/#state={state}&code=C"}, follow_redirects=False)

    assert respuesta.headers["location"] == "/plantilla"
    sesion_db.expire_all()
    assert estado_conexion(sesion_db, usuario.id) is EstadoConexion.CONECTADA
    assert _fernet().decrypt(_buscar(sesion_db, usuario.id).refresh_token_cifrado).decode() == "refresh-1"


def test_reconexion_con_otra_cuenta_de_laliga_no_toca_nada(sesion_db, cuenta_social, cliente_autenticado, laliga_canjea):
    """S11"""
    usuario, _ = cuenta_social
    _marcar_reconectar(sesion_db, usuario.id)
    refresh_antes = _buscar(sesion_db, usuario.id).refresh_token_cifrado
    state = _iniciar_reconexion(cliente_autenticado)
    laliga_canjea["oid"] = "oid-de-otra-persona"

    respuesta = cliente_autenticado.post("/token/social", data={"pegado": f"https://jwt.ms/#state={state}&code=C"})

    assert respuesta.status_code == 400
    assert "otra cuenta de LaLiga" in respuesta.text
    sesion_db.expire_all()
    fila = _buscar(sesion_db, usuario.id)
    assert fila.refresh_token_cifrado == refresh_antes
    assert fila.ultimo_error == ERROR_RECONEXION


def test_no_se_puede_usar_el_intento_de_otro(sesion_db, cuenta_social, cliente_autenticado, laliga_canjea):
    otro = Usuario(email=f"otro-{uuid.uuid4().hex[:6]}@example.com", password_hash="x")
    sesion_db.add(otro)
    sesion_db.commit()
    try:
        ajeno, _ = intentos.crear(sesion_db, usuario_id=otro.id)
        respuesta = cliente_autenticado.post("/token/social", data={"pegado": f"https://jwt.ms/#state={ajeno.state}&code=C"})
        assert respuesta.status_code == 400
        assert "Vuelve al paso 1" in respuesta.text
        assert laliga_canjea["llamadas"] == []
    finally:
        sesion_db.execute(delete(Usuario).where(Usuario.id == otro.id))
        sesion_db.commit()


def test_una_cuenta_de_contrasena_no_usa_la_reconexion_social(cliente_autenticado):
    respuesta = cliente_autenticado.post("/token/social/iniciar", follow_redirects=False)
    assert respuesta.headers["location"] == "/token"


# --------------------------------------------------------------------------------------
# T8: admin y cron (S12, S14)


def _cargar_cron():
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "snapshot_diario.py"
    spec = importlib.util.spec_from_file_location("snapshot_diario_social", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_el_cron_incluye_las_cuentas_sociales(sesion_db, cuenta_social):
    """S12"""
    usuario, _ = cuenta_social
    cron = _cargar_cron()
    assert usuario.id in {u.id for u in cron._usuarios_con_credenciales(sesion_db, None)}


def test_una_cuenta_social_por_reconectar_sale_en_la_vigilancia(sesion_db, cuenta_social):
    """S14: mismo canal que las de contraseña, sin código nuevo en la vigilancia."""
    usuario, _ = cuenta_social
    _marcar_reconectar(sesion_db, usuario.id)
    estados = [e for e in consultas.estado_tokens(sesion_db) if e.user_id == usuario.id]
    comprobacion = vigilancia.comprobar_logins(estados)
    assert comprobacion.ok is False
    assert ERROR_RECONEXION in comprobacion.detalle


def test_detalle_por_usuario_lleva_el_tipo_de_cuenta(sesion_db, cuenta_social):
    usuario, _ = cuenta_social
    detalle = {d.user_id: d for d in consultas.detalle_por_usuario(sesion_db, desde=datetime.now(timezone.utc) - timedelta(days=7))}
    assert detalle[usuario.id].tipo_cuenta == TipoCredenciales.SOCIAL.value


def test_uso_e_invitacion_muestran_el_tipo_de_cuenta(sesion_db, cuenta_social, cliente_autenticado, admin_es, invitacion):
    """S14"""
    usuario, _ = cuenta_social
    admin_es(usuario.email)
    fila, _ = invitacion
    invitaciones.marcar_usada(fila, usuario.id)
    sesion_db.commit()

    uso = cliente_autenticado.get("/uso").text
    assert "<th>Conexión</th>" in uso and "Red social" in uso
    assert "Red social" in cliente_autenticado.get("/invitaciones").text


def test_el_paso_2_avisa_de_no_escribir_en_jwt_ms(cliente, invitacion):
    """Descubierto en la primera prueba real: "Enter token below" parece pedir algo."""
    _, token = invitacion
    html = _iniciar(cliente, token).text
    assert "Enter token below" in html
    assert "no escribas nada en esa página" in html
