"""Panel /uso (specs/observabilidad, T12-T13: O14-O18)."""

from __future__ import annotations

import pytest


def test_el_admin_ve_el_panel(cliente_autenticado, usuario_de_prueba, admin_es):
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email.upper())  # mayúsculas en la variable: no debe importar

    respuesta = cliente_autenticado.get("/uso")
    assert respuesta.status_code == 200
    assert "<th>Alta</th>" in respuesta.text  # specs/registro: cuándo se creó cada cuenta


def test_otro_usuario_vuelve_a_su_plantilla(cliente_autenticado, otro_cliente, usuario_de_prueba, admin_es):
    """specs/registro A3: ni 404 ni 403; el usuario no debe notar que aquí hay algo."""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)

    respuesta = otro_cliente.get("/uso", follow_redirects=False)

    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/plantilla"


def test_sin_sesion_redirige_al_login(cliente, admin_es):
    admin_es("alguien@example.com")
    respuesta = cliente.get("/uso", follow_redirects=False)

    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/login"


def _enlace_uso(html: str) -> bool:
    return 'href="/uso"' in html


def test_el_admin_ve_el_enlace_en_el_menu(cliente_autenticado, usuario_de_prueba, admin_es):
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    assert _enlace_uso(cliente_autenticado.get("/token").text)


def test_otro_usuario_no_ve_el_enlace(otro_cliente, usuario_de_prueba, admin_es):
    """Ni debe saber que existe."""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    assert not _enlace_uso(otro_cliente.get("/token").text)


def test_sin_sesion_no_se_ve_el_enlace(cliente, admin_es):
    admin_es("alguien@example.com")
    assert not _enlace_uso(cliente.get("/login").text)


def test_sin_admin_configurado_nadie_pasa(cliente_autenticado, admin_es):
    """Fallo cerrado: olvidarse de la variable en Render oculta el panel, no lo abre."""
    admin_es("")
    respuesta = cliente_autenticado.get("/uso", follow_redirects=False)
    assert respuesta.status_code == 302
    assert respuesta.headers["location"] == "/plantilla"


# --------------------------------------------------------------------------------------
# T13: contenido del panel (O15-O18)
# --------------------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from sqlalchemy import delete  # noqa: E402

from fantasy.observabilidad import consultas  # noqa: E402
from fantasy.storage.modelos import EventoUso, TipoEvento  # noqa: E402


@pytest.fixture
def panel(cliente_autenticado, usuario_de_prueba, admin_es, sesion_db):
    """Cliente del admin con `evento_uso` vacía, y un ayudante para sembrar eventos."""
    usuario, _ = usuario_de_prueba
    admin_es(usuario.email)
    sesion_db.execute(delete(EventoUso))
    sesion_db.commit()

    def sembrar(hace: timedelta, ruta="/plantilla", *, ms=40, estado=200,
                tipo=TipoEvento.NAVEGACION, accion=None):
        sesion_db.add(EventoUso(
            user_id=usuario.id, ruta=ruta, tipo=tipo, accion=accion, duracion_ms=ms,
            estado_http=estado, creado_en=datetime.now(timezone.utc) - hace,
        ))
        sesion_db.commit()

    cliente_autenticado.sembrar = sembrar
    yield cliente_autenticado

    sesion_db.execute(delete(EventoUso))
    sesion_db.commit()


def test_sin_datos_el_panel_no_pinta_ceros_enganosos(panel):
    """O18: base recién migrada → 200 y "sin datos", nunca "0 s" ni una mediana de 0."""
    respuesta = panel.get("/uso")

    assert respuesta.status_code == 200
    html = respuesta.text
    assert html.count("Sin datos todavía") >= 3  # sesiones, secciones, velocidad
    assert "0 s" not in html


def test_los_numeros_del_panel_cuadran_con_las_consultas(panel, sesion_db):
    """O15-O17: lo que se ve es exactamente lo que devuelve la consulta."""
    panel.sembrar(timedelta(minutes=12), "/plantilla")
    panel.sembrar(timedelta(minutes=8), "/mercado")
    panel.sembrar(timedelta(minutes=2), "/plantilla")
    panel.sembrar(timedelta(minutes=1), "/plantilla/tabla", tipo=TipoEvento.ACCION, accion="actualizar_datos")
    panel.sembrar(timedelta(minutes=1), "/clausulas", ms=5200)

    html = panel.get("/uso").text

    ahora = datetime.now(timezone.utc)
    activos = consultas.usuarios_activos(sesion_db, ahora=ahora)
    resumen = consultas.resumen_sesiones(sesion_db, desde=ahora - timedelta(days=7))

    assert f'<span class="indicador__valor">{activos.hoy}</span>' in html
    assert resumen.total == 1
    assert "11 min 00 s" in html, "una sesión de 12 a 1 minutos atrás dura 11 minutos"
    assert "Plantilla" in html and "Mercado" in html and "Clausulazos" in html
    assert "Actualizar datos en Plantilla" in html
    assert "5200 ms" in html  # la lenta se ve en la tabla de velocidad


def test_una_duracion_muy_corta_no_se_lee_como_cero():
    """Encontrado en el end-to-end: truncar 0,4 s a "0 s" parece "sin dato"."""
    from fantasy.observabilidad.rutas import _duracion

    assert _duracion(timedelta(milliseconds=400)) == "menos de 1 s"
    assert _duracion(None) == "sin dato"
    assert _duracion(timedelta(minutes=11)) == "11 min 00 s"


def test_el_periodo_desconocido_cae_en_7_dias(panel):
    """Un `?dias=` inventado no rompe nada: se ignora."""
    html = panel.get("/uso?dias=9999").text
    assert 'aria-current="page">Últimos 7 días' in html


def test_el_panel_no_se_registra_a_si_mismo(panel, sesion_db):
    """O3: mirar el panel no puede aparecer como uso de la app."""
    panel.get("/uso")
    panel.get("/uso?dias=30")

    sesion_db.expire_all()
    assert sesion_db.query(EventoUso).filter(EventoUso.ruta == "/uso").count() == 0
