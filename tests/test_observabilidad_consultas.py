"""Consultas de observabilidad (specs/observabilidad, T9: O5, O12, O15-O17).

Los eventos se insertan con `creado_en` explícito y todas las consultas reciben el momento
de referencia: nada depende del reloj real. Las ejecuciones del cron usan fechas de 2099
para poder limpiar sin tocar ejecuciones reales de la base local.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from fantasy.auth.passwords import hashear_password
from fantasy.observabilidad import consultas
from fantasy.storage.fechas import MADRID
from fantasy.storage.modelos import EjecucionCron, EventoUso, TipoEvento, Usuario

# Un martes cualquiera, mediodía en Madrid.
AHORA = datetime(2026, 3, 10, 12, 0, tzinfo=MADRID)
HACE_30_DIAS = AHORA - timedelta(days=30)


@pytest.fixture
def bd(sesion_db):
    """Parte de `evento_uso` vacía y crea las cuentas que el test pida; lo borra todo al salir."""
    sesion_db.execute(delete(EventoUso))
    sesion_db.commit()
    creados: list[Usuario] = []

    def usuario() -> Usuario:
        u = Usuario(email=f"obs-{uuid.uuid4().hex[:8]}@example.com", password_hash=hashear_password("x" * 12))
        sesion_db.add(u)
        sesion_db.commit()
        creados.append(u)
        return u

    def evento(u: Usuario | None, momento: datetime, ruta="/plantilla", *, estado=200, ms=50,
               tipo=TipoEvento.NAVEGACION, accion=None):
        sesion_db.add(EventoUso(
            user_id=u.id if u else None, ruta=ruta, tipo=tipo, accion=accion,
            duracion_ms=ms, estado_http=estado, creado_en=momento,
        ))
        sesion_db.commit()

    sesion_db.usuario, sesion_db.evento = usuario, evento
    yield sesion_db

    sesion_db.execute(delete(EventoUso))
    for u in creados:
        sesion_db.delete(sesion_db.get(Usuario, u.id))
    sesion_db.commit()


# --- O5: sesiones derivadas --------------------------------------------------------------


@pytest.mark.parametrize(
    ("hueco", "umbral", "esperadas"),
    [
        (timedelta(minutes=5), timedelta(minutes=30), 1),
        (timedelta(minutes=31), timedelta(minutes=30), 2),
        # Mismo dato, otro umbral: se reagrupa SIN reescribir nada (el porqué de D5).
        (timedelta(minutes=31), timedelta(minutes=60), 1),
    ],
)
def test_las_sesiones_se_cortan_por_inactividad(bd, hueco, umbral, esperadas):
    u = bd.usuario()
    bd.evento(u, AHORA - hueco)
    bd.evento(u, AHORA)

    assert len(consultas.sesiones(bd, desde=HACE_30_DIAS, umbral=umbral)) == esperadas


def test_la_actividad_de_otro_usuario_no_alarga_una_sesion(bd):
    """Ana entra, se va 40 minutos y vuelve: son 2 sesiones. Que Bea entre en medio no
    puede "rellenar" el hueco de Ana y convertirlas en una."""
    ana, bea = bd.usuario(), bd.usuario()
    bd.evento(ana, AHORA)
    bd.evento(bea, AHORA + timedelta(minutes=20))
    bd.evento(ana, AHORA + timedelta(minutes=40))

    de_ana = [s for s in consultas.sesiones(bd, desde=HACE_30_DIAS) if s.user_id == ana.id]
    assert len(de_ana) == 2


def test_las_peticiones_anonimas_no_forman_sesiones(bd):
    bd.evento(None, AHORA, estado=302)
    assert consultas.sesiones(bd, desde=HACE_30_DIAS) == []


def test_una_sesion_de_una_pagina_tiene_duracion_desconocida_no_cero(bd):
    """Invariante nº 2: se sabe cuándo llegó, no cuándo se fue. No entra en la mediana."""
    u = bd.usuario()
    bd.evento(u, AHORA - timedelta(hours=5))                      # sesión de 1 página
    bd.evento(u, AHORA - timedelta(minutes=10))                   # sesión de 10 min
    bd.evento(u, AHORA)

    resumen = consultas.resumen_sesiones(bd, desde=HACE_30_DIAS)

    assert resumen.total == 2
    assert resumen.de_una_pagina == 1
    assert resumen.mediana == timedelta(minutes=10), "la sesión de 1 página no debe arrastrarla a 0"


def test_sin_eventos_el_resumen_no_inventa_ceros(bd):
    """O18 en la capa de datos: sin sesiones, la mediana es `None`, no `0 s`."""
    resumen = consultas.resumen_sesiones(bd, desde=HACE_30_DIAS)
    assert (resumen.total, resumen.mediana, resumen.p95) == (0, None, None)


# --- O15: usuarios y secciones -----------------------------------------------------------


def test_usuarios_activos_por_periodo(bd):
    hoy, semana, mes = bd.usuario(), bd.usuario(), bd.usuario()
    bd.evento(hoy, AHORA - timedelta(hours=1))
    bd.evento(semana, AHORA - timedelta(days=3))
    bd.evento(mes, AHORA - timedelta(days=20))
    bd.evento(None, AHORA, estado=302)  # un rebote al login no es un usuario

    assert consultas.usuarios_activos(bd, ahora=AHORA) == consultas.UsuariosActivos(1, 2, 3)


def test_hoy_es_el_dia_de_madrid_no_el_de_utc(bd):
    """A las 23:30 UTC ya es "mañana" en Madrid. Render corre en UTC: si esto usara la
    fecha UTC, la visita de las 00:30 de Madrid contaría para el día anterior."""
    u = bd.usuario()
    madrugada_utc = datetime(2026, 3, 9, 23, 30, tzinfo=timezone.utc)  # 00:30 del 10 en Madrid
    bd.evento(u, madrugada_utc)

    assert consultas.usuarios_activos(bd, ahora=AHORA).hoy == 1


def test_uso_por_ruta_distingue_navegacion_y_acciones(bd):
    u = bd.usuario()
    bd.evento(u, AHORA, "/plantilla")
    bd.evento(u, AHORA, "/plantilla")
    bd.evento(u, AHORA, "/plantilla/tabla", tipo=TipoEvento.ACCION, accion="actualizar_datos")
    bd.evento(None, AHORA, "/plantilla", estado=302)  # anónimo: no cuenta

    uso = consultas.uso_por_ruta(bd, desde=HACE_30_DIAS)

    assert uso == [
        consultas.UsoDeRuta("/plantilla", TipoEvento.NAVEGACION, None, 2),
        consultas.UsoDeRuta("/plantilla/tabla", TipoEvento.ACCION, "actualizar_datos", 1),
    ]


def test_el_detalle_incluye_a_quien_no_ha_entrado(bd):
    """O16. Que alguien no aparezca nunca es información: ocultarlo la escondería."""
    activo, ausente = bd.usuario(), bd.usuario()
    bd.evento(activo, AHORA - timedelta(minutes=5), "/mercado")
    bd.evento(activo, AHORA, "/mercado")
    bd.evento(activo, AHORA, "/plantilla")

    detalle = {d.user_id: d for d in consultas.detalle_por_usuario(bd, desde=HACE_30_DIAS)}

    assert detalle[activo.id].sesiones == 1
    assert detalle[activo.id].seccion_favorita == "/mercado"
    assert detalle[ausente.id].sesiones == 0
    assert detalle[ausente.id].ultima_visita is None
    assert detalle[ausente.id].seccion_favorita is None


# --- O17: salud ---------------------------------------------------------------------------


def test_latencia_en_percentiles_y_errores(bd):
    u = bd.usuario()
    for ms in (10, 20, 30, 40, 5000):  # un cold start de Render
        bd.evento(u, AHORA, "/token", ms=ms)
    bd.evento(u, AHORA, "/token", estado=500)

    (fila,) = consultas.latencia_por_ruta(bd, desde=HACE_30_DIAS)

    assert fila.peticiones == 6
    assert fila.p50_ms < 100, "la mediana no debe dejarse arrastrar por el cold start"
    assert fila.p95_ms > 1000, "pero el p95 sí debe delatarlo"
    assert fila.errores_5xx == 1


# --- O12: cobertura del emparejamiento ---------------------------------------------------


@pytest.fixture
def cron(sesion_db):
    def _borrar():
        sesion_db.execute(delete(EjecucionCron).where(EjecucionCron.iniciado_en >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
        sesion_db.commit()

    _borrar()

    def ejecucion(dia: int, hora: int, resultado: str, jugadores=None, con=None, sin=None):
        sesion_db.add(EjecucionCron(
            iniciado_en=datetime(2099, 1, dia, hora, 0, tzinfo=MADRID),
            snapshot_resultado=resultado,
            jugadores_escritos=jugadores, con_analitica=con, sin_emparejar=sin,
        ))
        sesion_db.commit()

    yield ejecucion
    _borrar()


def test_la_cobertura_solo_cuenta_ejecuciones_que_guardaron_snapshot(sesion_db, cron):
    """Reproduce el calendario REAL del cron en verano (CLAUDE.md §Flujo de trabajo): tras
    el disparo que guarda el snapshot llega otro que lo encuentra ya hecho (`ya_existia`) y
    deja los contadores a NULL. Si la consulta cogiera "la última ejecución del día" sin
    filtrar, la cobertura desaparecería casi todos los días."""
    cron(1, 2, "mercado_abierto")                                   # madrugada: sin contadores
    cron(1, 18, "guardado", jugadores=20, con=19, sin=1)
    cron(1, 19, "ya_existia")                                       # segundo disparo de tarde
    cron(2, 2, "mercado_abierto")
    cron(2, 18, "guardado", jugadores=20, con=10, sin=10)           # algo se rompió upstream
    cron(2, 19, "ya_existia")

    serie = consultas.cobertura_diaria(sesion_db, hasta=date(2099, 1, 2), dias=14)

    assert [(c.fecha, c.porcentaje) for c in serie] == [(date(2099, 1, 1), 95.0), (date(2099, 1, 2), 50.0)]


def test_un_dia_sin_jugadores_es_sin_dato_no_cero_por_ciento(sesion_db, cron):
    cron(1, 18, "guardado", jugadores=0, con=0, sin=0)

    (dia,) = consultas.cobertura_diaria(sesion_db, hasta=date(2099, 1, 1))

    assert dia.porcentaje is None
