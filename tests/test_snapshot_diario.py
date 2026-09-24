"""Paso 9 — job del snapshot diario: guardias, idempotencia y degradación."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, select

from fantasy.analytics.presentacion import BloqueAnalitico, JugadorPresentado
from fantasy.storage.modelos import EjecucionCron, EstadoAnalitica, Jugador, SnapshotMercado

# El job es un script, no un módulo del paquete: se carga por ruta.
_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "snapshot_diario.py"
_spec = importlib.util.spec_from_file_location("snapshot_diario", _RUTA)
snapshot_diario = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapshot_diario)

# Fecha deliberadamente imposible: los tests NO pueden borrar snapshots reales al
# limpiar. Usar la fecha de hoy hacía que la suite destruyera datos del usuario.
HOY = date(2099, 1, 1)


class _SubastaFalsa:
    def __init__(self, jugador):
        self.jugador = jugador
        self.precio_venta = 1_100_000
        self.expira_en = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


class _OficialFalso:
    def __init__(self):
        self.id = "test-snap-1"
        self.nombre = "Jugador Snapshot"
        self.apodo = "Snapshot"
        self.slug = "jugador-snapshot"
        self.equipo_id = 12
        self.posicion = "Defensa"
        self.valor_mercado = 1_000_000
        self.estado = "ok"


@pytest.fixture(autouse=True)
def _sin_registro_de_ejecucion(request, monkeypatch):
    """Los tests que ejecutan `main()` no deben dejar filas en `ejecucion_cron`.

    Cada ejecucion del script anota su resultado (specs/observabilidad, D7), asi que sin
    esto cada `pytest` ensuciaria la base local. Los tests del propio registro lo piden
    de vuelta con el marcador `registro_real`.
    """
    if request.node.get_closest_marker("registro_real"):
        return
    monkeypatch.setattr(snapshot_diario, "registrar_ejecucion", lambda fabrica, resultado: None)


@pytest.fixture
def limpio(sesion_db):
    """Deja la fecha de prueba sin snapshots, antes y después."""
    def _borrar():
        sesion_db.execute(delete(SnapshotMercado).where(SnapshotMercado.fecha == HOY))
        sesion_db.execute(delete(Jugador).where(Jugador.id == "test-snap-1"))
        sesion_db.commit()

    _borrar()
    yield sesion_db
    _borrar()


def _presentado(con_analitica: bool) -> JugadorPresentado:
    if con_analitica:
        from fantasy.analytics.presentacion import ProbabilidadJugar, TendenciaValor
        bloque = BloqueAnalitico.desde_scraping(
            tendencia_valor=TendenciaValor(direccion="sube", variacion_euros=5000),
            probabilidad_jugar=ProbabilidadJugar(porcentaje=60),
            origen="futbolfantasy.com",
            capturado_en=datetime.now(timezone.utc),
        )
    else:
        bloque = BloqueAnalitico.no_disponible("scraping caído", origen="futbolfantasy.com")

    return JugadorPresentado(
        id_oficial="test-snap-1", nombre="Snapshot", equipo="Málaga", posicion="Defensa",
        valor_mercado_euros=1_000_000, analitica=bloque,
    )


def test_scraping_caido_guarda_igual_los_valores_oficiales(limpio):
    """**R45**: la analítica es prescindible; el dato oficial no."""
    oficial = _OficialFalso()

    escritos = snapshot_diario._guardar(
        limpio, HOY, [_SubastaFalsa(oficial)], [_presentado(con_analitica=False)]
    )

    assert escritos == 1
    fila = limpio.scalar(select(SnapshotMercado).where(SnapshotMercado.fecha == HOY))
    assert fila.valor_mercado == 1_000_000, "el valor oficial se guarda igual"
    assert fila.precio_venta == 1_100_000
    assert fila.analitica_estado is EstadoAnalitica.NO_DISPONIBLE
    assert fila.analitica_motivo
    # Y nunca un 0 que se confunda con "no varió".
    assert fila.tendencia_variacion_euros is None
    assert fila.probabilidad_jugar is None


def test_con_analitica_se_guardan_ambos_bloques(limpio):
    oficial = _OficialFalso()

    snapshot_diario._guardar(limpio, HOY, [_SubastaFalsa(oficial)], [_presentado(True)])

    fila = limpio.scalar(select(SnapshotMercado).where(SnapshotMercado.fecha == HOY))
    assert fila.valor_mercado == 1_000_000
    assert fila.analitica_estado is EstadoAnalitica.DISPONIBLE
    assert fila.probabilidad_jugar == 60
    assert fila.tendencia_variacion_euros == 5000


@pytest.mark.parametrize(
    ("momento_utc", "guarda_snapshot"),
    [
        (datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc), False),   # 17:00 Madrid (CEST)
        (datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc), True),   # 18:30 Madrid (CEST)
        (datetime(2026, 1, 28, 16, 30, tzinfo=timezone.utc), False),  # 17:30 Madrid (CET)
        (datetime(2026, 1, 28, 17, 30, tzinfo=timezone.utc), True),   # 18:30 Madrid (CET)
    ],
)
def test_la_guardia_horaria_solo_afecta_al_snapshot_de_mercado(momento_utc, guarda_snapshot):
    """R42: el mismo cron UTC cae antes o despues del cierre segun la estacion.

    Desde que hay disparos de madrugada, la guardia ya NO corta la ejecucion entera: la
    analitica se refresca siempre (es lo que consultan las pantallas) y solo el snapshot
    de mercado espera al cierre de las 18:00.
    """
    with patch.object(snapshot_diario.sys, "argv", ["snapshot_diario.py"]):
        with patch.object(snapshot_diario, "ahora_en_madrid", return_value=momento_utc):
            with patch.object(snapshot_diario, "_usuarios_con_credenciales", return_value=["u"]):
                with patch.object(
                    snapshot_diario, "scrapear_todo_para_guardar", return_value=([], {})
                ):
                    with patch.object(snapshot_diario, "_ya_hay_snapshot", return_value=False):
                        with patch.object(
                            snapshot_diario, "_guardar_snapshot_de_mercado"
                        ) as guardar:
                            with patch.object(snapshot_diario, "purgar_snapshots_antiguos", return_value=0):
                                with patch.object(snapshot_diario, "purgar_analitica_antigua", return_value=0), \
                                    patch.object(snapshot_diario, "purgar_eventos_uso", return_value=0):
                                    snapshot_diario.main()

    assert guardar.called is guarda_snapshot


def test_la_analitica_se_refresca_aunque_el_mercado_no_haya_cerrado():
    """El disparo de madrugada existe justo para esto: el valor cambia a las 00:00 y la
    analitica debe reflejarlo, sin esperar al cierre de las 18:00."""
    madrugada = datetime(2026, 8, 28, 0, 30, tzinfo=timezone.utc)  # 02:30 Madrid

    with patch.object(snapshot_diario.sys, "argv", ["snapshot_diario.py"]):
        with patch.object(snapshot_diario, "ahora_en_madrid", return_value=madrugada):
            with patch.object(snapshot_diario, "_usuarios_con_credenciales", return_value=["u"]):
                with patch.object(
                    snapshot_diario, "scrapear_todo_para_guardar", return_value=(["t"], {})
                ):
                    with patch.object(
                        snapshot_diario, "guardar_analitica_del_dia", return_value=669
                    ) as guardar_analitica:
                        with patch.object(snapshot_diario, "_ya_hay_snapshot", return_value=False):
                            with patch.object(snapshot_diario, "_guardar_snapshot_de_mercado") as snap:
                                with patch.object(snapshot_diario, "purgar_snapshots_antiguos", return_value=0):
                                    with patch.object(snapshot_diario, "purgar_analitica_antigua", return_value=0), \
                                    patch.object(snapshot_diario, "purgar_eventos_uso", return_value=0):
                                        snapshot_diario.main()

    guardar_analitica.assert_called_once()
    assert not snap.called, "de madrugada no se toca el snapshot de mercado"


def test_solo_se_usan_usuarios_con_credenciales(sesion_db, usuario_de_prueba):
    """El cron no puede pedir un token a nadie: sin credenciales, ese usuario no sirve."""
    usuario, _ = usuario_de_prueba

    encontrados = snapshot_diario._usuarios_con_credenciales(sesion_db, usuario.email)

    assert encontrados == [], "sin credenciales guardadas no debe seleccionarse"


def test_la_deteccion_de_snapshot_ignora_la_analitica(limpio):
    """`_ya_hay_snapshot` solo mira market_snapshot a proposito.

    Antes exigia tambien analitica, para que un dia a medias se regenerase. Con los
    disparos de madrugada esa proteccion sobra: la analitica se refresca en cada
    ejecucion, asi que mezclarlas impedia recapturarla cuando ya habia snapshot.
    """
    assert snapshot_diario._ya_hay_snapshot(limpio, HOY) is False

    snapshot_diario._guardar(limpio, HOY, [_SubastaFalsa(_OficialFalso())], [_presentado(True)])

    assert snapshot_diario._ya_hay_snapshot(limpio, HOY) is True


# --------------------------------------------------------------------------------------
# Registro de ejecuciones (specs/observabilidad: O10, O11, O13)
# --------------------------------------------------------------------------------------

# Igual que HOY: fecha imposible para poder limpiar sin tocar ejecuciones reales.
MOMENTO_FUTURO = datetime(2099, 1, 1, 17, 0, tzinfo=timezone.utc)  # 18:00 Madrid (CET)


@pytest.fixture
def ejecuciones(sesion_db):
    """Lee las filas de `ejecucion_cron` de la fecha de prueba y las borra antes y después."""
    def _borrar():
        sesion_db.execute(delete(EjecucionCron).where(EjecucionCron.iniciado_en >= datetime(2099, 1, 1, tzinfo=timezone.utc)))
        sesion_db.commit()

    _borrar()

    def _leer() -> list[EjecucionCron]:
        sesion_db.expire_all()
        return list(sesion_db.scalars(
            select(EjecucionCron).where(EjecucionCron.iniciado_en >= datetime(2099, 1, 1, tzinfo=timezone.utc))
        ))

    yield _leer
    _borrar()


def _correr_main(*, momento=MOMENTO_FUTURO, argv=("snapshot_diario.py",), **parches):
    """Ejecuta `main()` con todo lo externo parcheado. `parches` sobreescribe los valores por
    defecto (un mercado ya guardado, scraping vacio, sin purgas)."""
    por_defecto = {
        "_usuarios_con_credenciales": ["u"],
        "scrapear_todo_para_guardar": (["t"], {}),
        "guardar_analitica_del_dia": 669,
        "_ya_hay_snapshot": False,
        "_guardar_snapshot_de_mercado": None,
        "purgar_snapshots_antiguos": 0,
        "purgar_analitica_antigua": 0,
        "purgar_eventos_uso": 0,
    }
    por_defecto.update(parches)

    from contextlib import ExitStack
    with ExitStack() as pila:
        pila.enter_context(patch.object(snapshot_diario.sys, "argv", list(argv)))
        pila.enter_context(patch.object(snapshot_diario, "ahora_en_madrid", return_value=momento))
        for nombre, valor in por_defecto.items():
            # Un invocable hace de `side_effect` (fingir efectos o excepciones); cualquier
            # otro valor es simplemente lo que devuelve la funcion parcheada.
            opciones = {"side_effect": valor} if callable(valor) else {"return_value": valor}
            pila.enter_context(patch.object(snapshot_diario, nombre, **opciones))
        snapshot_diario.main()


@pytest.mark.registro_real
def test_una_ejecucion_deja_su_fila_con_los_contadores(ejecuciones):
    """O10: la fila refleja lo que el script hizo, incluido `sin_emparejar` (R27)."""
    def _guarda_el_mercado(sesion, usuarios, dia, resultado):
        resultado.snapshot_resultado = "guardado"
        resultado.jugadores_escritos = 13
        resultado.con_analitica = 12
        resultado.sin_emparejar = 1

    _correr_main(_guardar_snapshot_de_mercado=_guarda_el_mercado)

    filas = ejecuciones()
    assert len(filas) == 1
    fila = filas[0]
    assert fila.snapshot_resultado == "guardado"
    assert (fila.analitica_guardados, fila.jugadores_escritos) == (669, 13)
    assert (fila.con_analitica, fila.sin_emparejar) == (12, 1)
    assert fila.error is None
    # No se compara con `iniciado_en`: en el test ese reloj esta falseado a 2099.
    assert fila.terminado_en is not None


@pytest.mark.registro_real
@pytest.mark.parametrize(
    ("kwargs", "esperado"),
    [
        # O11: una ejecucion que no hace nada TAMBIEN deja fila, con el motivo.
        (dict(momento=datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)), "mercado_abierto"),
        (dict(_ya_hay_snapshot=True), "ya_existia"),
        (dict(_usuarios_con_credenciales=[]), "sin_usuarios"),
    ],
)
def test_una_ejecucion_que_no_hace_nada_tambien_deja_fila(ejecuciones, kwargs, esperado):
    """O11: no se distingue "no corrio" de "corrio y decidio no hacer nada"."""
    _correr_main(**kwargs)

    filas = ejecuciones()
    assert len(filas) == 1
    assert filas[0].snapshot_resultado == esperado
    assert filas[0].error is None


@pytest.mark.registro_real
def test_una_ejecucion_que_muere_con_systemexit_deja_fila_con_el_error(ejecuciones):
    """El motivo de ser del `finally` (D7): las ejecuciones que fallan son las que importan.

    Es el caso real "ningun usuario pudo leer el mercado", que acaba en `SystemExit(1)`.
    """
    def _no_puede_leer_el_mercado(sesion, usuarios, dia, resultado):
        resultado.snapshot_resultado = "error"
        resultado.error = "ningun usuario pudo leer el mercado"
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        _correr_main(_guardar_snapshot_de_mercado=_no_puede_leer_el_mercado)

    filas = ejecuciones()
    assert len(filas) == 1
    assert filas[0].snapshot_resultado == "error"
    assert filas[0].error == "ningun usuario pudo leer el mercado"


@pytest.mark.registro_real
def test_una_excepcion_inesperada_deja_fila_y_se_relanza(ejecuciones):
    """Cualquier otro fallo tambien deja rastro, y el script sigue muriendo con su error
    (el cron tiene que salir en rojo, no tragarselo)."""
    def _explota(*args, **kwargs):
        raise RuntimeError("el sitio devolvio basura")

    with pytest.raises(RuntimeError, match="basura"):
        _correr_main(scrapear_todo_para_guardar=_explota)

    filas = ejecuciones()
    assert len(filas) == 1
    assert filas[0].snapshot_resultado == "error"  # el valor por defecto es pesimista
    assert filas[0].error == "RuntimeError: el sitio devolvio basura"


@pytest.mark.registro_real
def test_si_el_registro_falla_el_snapshot_se_guarda_igual(monkeypatch, limpio):
    """O13: anotar la ejecucion es accesorio; jamas puede impedir guardar el mercado."""
    from fantasy.storage import ejecucion_repo

    def _fabrica_rota():
        raise RuntimeError("base de datos caida")

    resultado = ejecucion_repo.ResultadoEjecucion(iniciado_en=MOMENTO_FUTURO)
    ejecucion_repo.registrar_ejecucion(_fabrica_rota, resultado)  # no debe lanzar

    # Y el snapshot, por su lado, sigue funcionando con normalidad.
    snapshot_diario._guardar(limpio, HOY, [_SubastaFalsa(_OficialFalso())], [_presentado(True)])
    assert snapshot_diario._ya_hay_snapshot(limpio, HOY) is True


@pytest.mark.registro_real
def test_un_resultado_por_defecto_se_anota_como_error(ejecuciones):
    """Un camino que se olvide de fijar el resultado sale como `error`, no como exito."""
    from fantasy.storage import ejecucion_repo
    from fantasy.storage.engine import obtener_fabrica_sesiones

    ejecucion_repo.registrar_ejecucion(
        obtener_fabrica_sesiones(), ejecucion_repo.ResultadoEjecucion(iniciado_en=MOMENTO_FUTURO)
    )
    assert ejecuciones()[0].snapshot_resultado == "error"
