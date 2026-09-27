"""Alta por invitación (specs/registro, bloque B)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from fantasy.auth import alta, invitaciones, laliga_login
from fantasy.auth.credenciales_store import _descifrar
from fantasy.auth.invitaciones import EstadoInvitacion
from fantasy.auth.passwords import verificar_password
from fantasy.auth.sesiones import NOMBRE_COOKIE, leer_usuario_id_de_cookie
from fantasy.auth.token_store import leer_token_descifrado
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import CredencialesLaLiga, EventoUso, Invitacion, TokenUsuario, Usuario

PASSWORD = "la-de-laliga-123"


@pytest.fixture
def email_nuevo(sesion_db):
    """Un email que no existe; borra al terminar la cuenta que se haya creado con él."""
    email = f"alta-{uuid.uuid4().hex[:8]}@example.com"
    yield email
    sesion_db.execute(delete(Usuario).where(Usuario.email == email))
    sesion_db.commit()


@pytest.fixture
def invitacion(sesion_db):
    fila, token = invitaciones.crear(sesion_db)
    yield fila, token
    sesion_db.execute(delete(Invitacion).where(Invitacion.id == fila.id))
    sesion_db.commit()


@pytest.fixture
def laliga_acepta(monkeypatch, jwt_falso):
    llamadas: list[str] = []

    def _iniciar(email: str, password: str) -> laliga_login.TokensLaLiga:
        llamadas.append(email)
        exp = datetime.now(timezone.utc) + timedelta(hours=24)
        return laliga_login.TokensLaLiga(jwt_falso(exp), "refresh-falso", exp)

    monkeypatch.setattr(laliga_login, "iniciar_sesion", _iniciar)
    return llamadas


@pytest.fixture
def laliga_lanza(monkeypatch):
    llamadas: list[str] = []

    def _fijar(excepcion: Exception) -> list[str]:
        def _iniciar(email: str, password: str):
            llamadas.append(email)
            raise excepcion

        monkeypatch.setattr(laliga_login, "iniciar_sesion", _iniciar)
        return llamadas

    return _fijar


def _usuario(sesion, email: str) -> Usuario | None:
    sesion.expire_all()
    return sesion.scalar(select(Usuario).where(Usuario.email == email))


def _estado(sesion, invitacion: Invitacion) -> EstadoInvitacion:
    sesion.expire_all()
    return invitaciones.estado(sesion.get(Invitacion, invitacion.id))


def test_alta_correcta_deja_todo_escrito(sesion_db, invitacion, email_nuevo, laliga_acepta):
    """A9: usuario, credenciales, token e invitación usada."""
    fila, token = invitacion

    usuario = alta.dar_de_alta(sesion_db, token, f"  {email_nuevo.upper()} ", PASSWORD)

    guardado = _usuario(sesion_db, email_nuevo)
    assert guardado is not None and guardado.id == usuario.id
    assert verificar_password(PASSWORD, guardado.password_hash)
    credenciales = sesion_db.scalar(select(CredencialesLaLiga).where(CredencialesLaLiga.user_id == usuario.id))
    assert _descifrar(credenciales) == (email_nuevo, PASSWORD)
    assert credenciales.refresh_token_cifrado is not None
    assert leer_token_descifrado(sesion_db, usuario.id) is not None
    assert _estado(sesion_db, fila) is EstadoInvitacion.USADA
    assert sesion_db.get(Invitacion, fila.id).usuario_id == usuario.id
    assert laliga_acepta == [email_nuevo]


def test_si_falla_la_ultima_escritura_no_queda_nada(sesion_db, invitacion, email_nuevo, laliga_acepta, monkeypatch):
    """A9: una sola transacción."""
    fila, token = invitacion

    def _explota(*_args, **_kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(invitaciones, "marcar_usada", _explota)
    with pytest.raises(RuntimeError):
        alta.dar_de_alta(sesion_db, token, email_nuevo, PASSWORD)

    assert _usuario(sesion_db, email_nuevo) is None
    assert sesion_db.scalar(select(TokenUsuario).join(Usuario).where(Usuario.email == email_nuevo)) is None
    assert _estado(sesion_db, fila) is EstadoInvitacion.PENDIENTE


@pytest.mark.parametrize(
    "excepcion",
    [laliga_login.CredencialesInvalidas("x"), laliga_login.LoginNoDisponible("x")],
    ids=["rechaza", "no_responde"],
)
def test_si_laliga_no_acepta_no_se_crea_nada(sesion_db, invitacion, email_nuevo, laliga_lanza, excepcion):
    """A10, A11: la invitación sigue pendiente para reintentar."""
    fila, token = invitacion
    laliga_lanza(excepcion)

    with pytest.raises(type(excepcion)):
        alta.dar_de_alta(sesion_db, token, email_nuevo, PASSWORD)

    assert _usuario(sesion_db, email_nuevo) is None
    assert _estado(sesion_db, fila) is EstadoInvitacion.PENDIENTE


def test_email_existente_no_llama_a_laliga(sesion_db, invitacion, usuario_de_prueba, laliga_acepta):
    """A12"""
    fila, token = invitacion
    usuario, _ = usuario_de_prueba

    with pytest.raises(alta.YaTieneCuenta):
        alta.dar_de_alta(sesion_db, token, usuario.email.upper(), PASSWORD)

    assert laliga_acepta == []
    assert _estado(sesion_db, fila) is EstadoInvitacion.PENDIENTE


def test_enlace_no_valido(sesion_db, email_nuevo, laliga_acepta):
    with pytest.raises(alta.EnlaceNoValido):
        alta.dar_de_alta(sesion_db, "no-existe", email_nuevo, PASSWORD)
    assert laliga_acepta == []


def test_dos_altas_simultaneas_con_el_mismo_enlace_crean_un_solo_usuario(sesion_db, invitacion, monkeypatch, jwt_falso):
    """A13, contra Postgres real: dos hilos, dos sesiones, dos emails distintos.

    LaLiga tarda a propósito para que la segunda alta llegue mientras la primera tiene la
    fila bloqueada.
    """
    _, token = invitacion
    emails = [f"carrera-{uuid.uuid4().hex[:8]}@example.com" for _ in range(2)]

    def _iniciar_lento(email: str, password: str) -> laliga_login.TokensLaLiga:
        time.sleep(0.5)
        exp = datetime.now(timezone.utc) + timedelta(hours=24)
        return laliga_login.TokensLaLiga(jwt_falso(exp), None, exp)

    monkeypatch.setattr(laliga_login, "iniciar_sesion", _iniciar_lento)
    resultados: list[str] = []

    def _alta(email: str) -> None:
        with obtener_fabrica_sesiones()() as sesion:
            try:
                alta.dar_de_alta(sesion, token, email, PASSWORD)
                resultados.append("ok")
            except alta.EnlaceNoValido:
                resultados.append("enlace_no_valido")

    hilos = [threading.Thread(target=_alta, args=(e,)) for e in emails]
    try:
        for hilo in hilos:
            hilo.start()
            time.sleep(0.1)  # el primero toma el bloqueo antes de que llegue el segundo
        for hilo in hilos:
            hilo.join(timeout=10)

        assert sorted(resultados) == ["enlace_no_valido", "ok"]
        creados = sesion_db.scalars(select(Usuario).where(Usuario.email.in_(emails))).all()
        assert len(creados) == 1
    finally:
        sesion_db.execute(delete(Usuario).where(Usuario.email.in_(emails)))
        sesion_db.commit()


# --------------------------------------------------------------------------------------
# T8: rutas (A7-A14)


def test_el_formulario_pide_email_y_contrasena_de_laliga(cliente, invitacion):
    """A7"""
    _, token = invitacion
    respuesta = cliente.get(f"/registro?invitacion={token}")
    assert respuesta.status_code == 200
    assert 'name="email"' in respuesta.text and 'name="password"' in respuesta.text
    assert "cifrados" in respuesta.text


@pytest.fixture(params=["inexistente", "caducada", "usada", "revocada", "sin_parametro"])
def enlace_no_valido(request, sesion_db, usuario_de_prueba):
    """A8: los cinco casos. Devuelve el token (o None) y limpia lo creado."""
    if request.param == "inexistente":
        yield "token-que-no-existe"
        return
    if request.param == "sin_parametro":
        yield None
        return
    ahora = datetime.now(timezone.utc)
    fila, token = invitaciones.crear(
        sesion_db, ahora=ahora - timedelta(days=8) if request.param == "caducada" else ahora
    )
    if request.param == "usada":
        invitaciones.marcar_usada(fila, usuario_de_prueba[0].id)
        sesion_db.commit()
    if request.param == "revocada":
        invitaciones.revocar(sesion_db, fila.id)
    yield token
    sesion_db.execute(delete(Invitacion).where(Invitacion.id == fila.id))
    sesion_db.commit()


def test_un_enlace_no_valido_no_muestra_formulario(cliente, enlace_no_valido):
    """A8: GET"""
    url = "/registro" if enlace_no_valido is None else f"/registro?invitacion={enlace_no_valido}"
    respuesta = cliente.get(url)
    assert "ha caducado o ya se ha usado" in respuesta.text
    assert "<form" not in respuesta.text.split("<main", 1)[1]


def test_un_enlace_no_valido_no_crea_nada(cliente, enlace_no_valido, email_nuevo, sesion_db, laliga_acepta):
    """A8: POST"""
    datos = {"email": email_nuevo, "password": PASSWORD}
    if enlace_no_valido is not None:
        datos["invitacion"] = enlace_no_valido
    respuesta = cliente.post("/registro", data=datos)
    assert respuesta.status_code == 400
    assert "ha caducado o ya se ha usado" in respuesta.text
    assert _usuario(sesion_db, email_nuevo) is None
    assert laliga_acepta == []


def test_alta_por_la_web_abre_sesion_y_lleva_a_la_plantilla(cliente, invitacion, email_nuevo, sesion_db, laliga_acepta):
    """A9"""
    _, token = invitacion
    respuesta = cliente.post(
        "/registro", data={"invitacion": token, "email": email_nuevo, "password": PASSWORD},
        follow_redirects=False,
    )
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/plantilla"
    usuario = _usuario(sesion_db, email_nuevo)
    assert leer_usuario_id_de_cookie(respuesta.cookies[NOMBRE_COOKIE]) == usuario.id


@pytest.mark.parametrize(
    ("excepcion", "codigo", "texto"),
    [
        (laliga_login.CredencialesInvalidas("x"), 400, "Google"),
        (laliga_login.LoginNoDisponible("x"), 503, "en un rato"),
    ],
    ids=["rechaza", "no_responde"],
)
def test_si_laliga_no_acepta_se_explica_y_se_puede_reintentar(cliente, invitacion, email_nuevo, laliga_lanza, excepcion, codigo, texto):
    """A10, A11: el formulario sigue ahí, con el email, sin la contraseña."""
    _, token = invitacion
    laliga_lanza(excepcion)
    respuesta = cliente.post("/registro", data={"invitacion": token, "email": email_nuevo, "password": PASSWORD})
    assert respuesta.status_code == codigo
    assert texto in respuesta.text
    assert f'value="{email_nuevo}"' in respuesta.text
    assert PASSWORD not in respuesta.text


def test_email_con_cuenta_manda_al_login(cliente, invitacion, usuario_de_prueba, laliga_acepta):
    """A12"""
    _, token = invitacion
    usuario, _ = usuario_de_prueba
    respuesta = cliente.post("/registro", data={"invitacion": token, "email": usuario.email, "password": PASSWORD})
    assert respuesta.status_code == 409
    assert 'href="/login"' in respuesta.text
    assert laliga_acepta == []


def test_ni_la_contrasena_ni_el_token_quedan_en_ningun_sitio(cliente, invitacion, email_nuevo, sesion_db, laliga_lanza, caplog, monkeypatch):
    """A14: logs, `evento_uso` y respuestas, en un alta fallida y en una correcta."""
    _, token = invitacion
    caplog.set_level(logging.DEBUG)
    antes = sesion_db.scalar(select(func.max(EventoUso.creado_en)))

    respuestas = [cliente.get(f"/registro?invitacion={token}")]
    laliga_lanza(laliga_login.CredencialesInvalidas("x"))
    respuestas.append(cliente.post("/registro", data={"invitacion": token, "email": email_nuevo, "password": PASSWORD}))

    def _acepta(email, password):
        exp = datetime.now(timezone.utc) + timedelta(hours=24)
        return laliga_login.TokensLaLiga("cabecera.e30.firma", None, exp)

    monkeypatch.setattr(laliga_login, "iniciar_sesion", _acepta)
    respuestas.append(cliente.post(
        "/registro", data={"invitacion": token, "email": email_nuevo, "password": PASSWORD},
        follow_redirects=False,
    ))
    assert respuestas[-1].status_code == 302

    for respuesta in respuestas:
        assert PASSWORD not in respuesta.text
    assert PASSWORD not in caplog.text
    # El token sí sale en el log de `httpx`, pero es el CLIENTE de los tests anotando la URL
    # que pide (el equivalente al log de acceso de Render, riesgo aceptado en design.md).
    # Lo que no puede es salir en un log de la app.
    assert not [r for r in caplog.records if r.name != "httpx" and token in r.getMessage()]

    sesion_db.expire_all()
    consulta = select(EventoUso)
    if antes is not None:
        consulta = consulta.where(EventoUso.creado_en > antes)
    for evento in sesion_db.scalars(consulta):
        fila = " ".join(str(v) for v in vars(evento).values())
        assert PASSWORD not in fila and token not in fila
