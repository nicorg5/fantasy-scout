"""Vigilancia diaria (specs/observabilidad, T10: O19-O20).

Las comprobaciones son puras: se alimentan con objetos construidos en memoria, sin tocar la
base de datos. Así no dependen de lo que haya en la BD local (que puede tener cuentas reales
con credenciales) y cada condición se prueba en verde y en rojo.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from fantasy.observabilidad.consultas import CoberturaDia, EstadoToken
from fantasy.observabilidad.vigilancia import (
    Comprobacion,
    Umbrales,
    comprobar_cobertura,
    comprobar_cron,
    comprobar_logins,
    comprobar_snapshot,
)
from fantasy.storage.modelos import EjecucionCron

AHORA = datetime(2026, 3, 10, 6, 0, tzinfo=timezone.utc)  # la hora del workflow
U = Umbrales()


def _ejecucion(hace_horas: float, resultado: str, error: str | None = None) -> EjecucionCron:
    return EjecucionCron(iniciado_en=AHORA - timedelta(hours=hace_horas), snapshot_resultado=resultado, error=error)


# --- Condición 1: el cron corre ----------------------------------------------------------


def test_cron_sano():
    assert comprobar_cron([_ejecucion(5, "mercado_abierto")], ahora=AHORA, umbrales=U).ok


def test_cron_que_no_ha_corrido():
    c = comprobar_cron([_ejecucion(30, "guardado")], ahora=AHORA, umbrales=U)
    assert not c.ok
    assert "no ha corrido" in c.detalle


@pytest.mark.parametrize("resultado", ["error", "sin_usuarios"])
def test_cron_que_corre_pero_siempre_falla(resultado):
    """`sin_usuarios` también es fallo: el cron corre, pero no puede hacer nada útil."""
    c = comprobar_cron([_ejecucion(5, resultado, "ningun usuario pudo leer el mercado")], ahora=AHORA, umbrales=U)
    assert not c.ok
    assert resultado in c.detalle


# --- Condición 2: se guarda el snapshot ----------------------------------------------------


def test_madrugadas_sanas_no_tapan_un_snapshot_que_lleva_dias_sin_guardarse():
    """La razón de ser de esta condición: la 1 da verde con las madrugadas, esta no."""
    ejecuciones = [
        _ejecucion(40, "error", "ningun usuario pudo leer el mercado"),  # tarde de anteayer
        _ejecucion(14, "error", "ningun usuario pudo leer el mercado"),  # tarde de ayer
        _ejecucion(5, "mercado_abierto"),                                # madrugada de hoy
    ]
    assert comprobar_cron(ejecuciones, ahora=AHORA, umbrales=U).ok
    assert not comprobar_snapshot(ejecuciones, ahora=AHORA, umbrales=U).ok


def test_snapshot_de_ayer_por_la_tarde_basta():
    assert comprobar_snapshot([_ejecucion(13.5, "guardado")], ahora=AHORA, umbrales=U).ok


# --- Condición 3: la cobertura no se desploma --------------------------------------------


def _serie(*porcentajes: float | None) -> list[CoberturaDia]:
    """Un día por valor, de 20 jugadores. `None` es un día con 0 jugadores (sin dato)."""
    dias = []
    for i, p in enumerate(porcentajes):
        jugadores = 0 if p is None else 20
        con = 0 if p is None else round(p * 20 / 100)
        dias.append(CoberturaDia(date(2026, 3, 1) + timedelta(days=i), jugadores, con, jugadores - con))
    return dias


def test_cobertura_estable():
    assert comprobar_cobertura(_serie(95, 95, 90, 95, 90), umbrales=U).ok


def test_caida_brusca_de_cobertura():
    """R27 del MVP: la señal de que futbolfantasy cambió algo y se degrada en silencio."""
    c = comprobar_cobertura(_serie(95, 95, 95, 95, 70), umbrales=U)
    assert not c.ok
    assert "70%" in c.detalle


def test_una_caida_pequena_no_es_alarma():
    assert comprobar_cobertura(_serie(95, 95, 95, 95, 85), umbrales=U).ok


def test_con_poca_historia_no_hay_alarma_sino_aviso():
    """Recién desplegado solo hay uno o dos días: compararlos sería ruido, no señal."""
    c = comprobar_cobertura(_serie(95, 40), umbrales=U)
    assert c.ok
    assert "datos insuficientes" in c.detalle


def test_los_dias_sin_dato_no_cuentan_como_cero():
    """Un día sin jugadores no puede hundir la media como si fuera 0%."""
    assert comprobar_cobertura(_serie(95, None, 95, 95, 95), umbrales=U).ok


# --- Condición 4: el login automático ------------------------------------------------------


def _estado(error: str | None, email="javi@example.com") -> EstadoToken:
    return EstadoToken(uuid.uuid4(), email, None, error)


def test_quien_no_abre_la_app_no_dispara_nada():
    """Sin `ultimo_login_ok` reciente pero sin error: no hay nada roto."""
    assert comprobar_logins([_estado(None)]).ok


def test_login_fallido_sin_recuperar():
    c = comprobar_logins([_estado(None), _estado("LaLiga rechazó las credenciales guardadas")])
    assert not c.ok
    assert "rechazó" in c.detalle


def test_la_alerta_identifica_por_id_nunca_por_email():
    """O20: la salida acaba en el log de GitHub Actions."""
    estado = _estado("login no disponible", email="secreto@example.com")
    c = comprobar_logins([estado])
    assert str(estado.user_id) in c.detalle
    assert "secreto@example.com" not in c.detalle


# --- El script ----------------------------------------------------------------------------

_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "vigilancia.py"
_spec = importlib.util.spec_from_file_location("vigilancia_script", _RUTA)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def test_el_script_sale_en_rojo_si_algo_falla(capsys):
    """Código ≠ 0 es lo que pone el workflow en rojo y hace que GitHub mande el email."""
    comprobaciones = [Comprobacion("cron", True, "bien"), Comprobacion("cobertura", False, "70% frente a 95%")]
    with patch.object(script, "evaluar", return_value=comprobaciones):
        assert script.main([]) == 1

    salida = capsys.readouterr().out
    assert "[ALERTA] cobertura" in salida
    assert "@" not in salida


def test_el_script_sale_en_verde_si_todo_va_bien(capsys):
    with patch.object(script, "evaluar", return_value=[Comprobacion("cron", True, "bien")]):
        assert script.main([]) == 0
    assert "Todo en orden" in capsys.readouterr().out


def test_los_umbrales_de_la_linea_de_comandos_llegan_a_la_evaluacion():
    """Es lo que permite forzar el rojo en T11 para comprobar que el email llega."""
    with patch.object(script, "evaluar", return_value=[]) as evaluar:
        script.main(["--caida-cobertura", "-1", "--horas-sin-snapshot", "1"])

    umbrales = evaluar.call_args.kwargs["umbrales"]
    assert (umbrales.caida_cobertura_puntos, umbrales.horas_sin_snapshot) == (-1, 1)
