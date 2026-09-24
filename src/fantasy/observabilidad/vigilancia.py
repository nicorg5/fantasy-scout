"""Vigilancia diaria: decide si algo va mal (specs/observabilidad, O19-O20, design.md §D9).

La ejecuta `scripts/vigilancia.py` desde GitHub Actions. Si alguna comprobación falla, el
script sale con código ≠ 0, el workflow queda en rojo y **GitHub manda el email**. No hay
más infraestructura de alertas que esa.

Cada comprobación es una **función pura**: recibe datos ya leídos y devuelve un veredicto.
Se prueban sin base de datos, en verde y en rojo, y no dependen de lo que haya en la BD
local. `evaluar()` es la única que lee de la BD.

Reglas de todas las comprobaciones:

- **Síntomas, no causas.** Se avisa de lo que se nota (no hay datos, no hay analítica, no
  se puede entrar a LaLiga), no de lo que *podría* causarlo.
- **Ventanas de horas, nunca "ayer a las X"**: GitHub Actions retrasa los crons hasta 2 h.
- **Datos insuficientes no es alarma.** Con menos historia de la necesaria, se dice y ya.
- **Ni un email en la salida** (O20): los usuarios se identifican por su id.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from fantasy.observabilidad import consultas
from fantasy.storage.fechas import fecha_local
from fantasy.storage.modelos import EjecucionCron

# Desenlaces del cron que significan "terminó haciendo su trabajo". `sin_usuarios` NO está:
# el cron corrió, pero no puede autenticarse, así que no puede hacer nada útil.
RESULTADOS_SANOS = frozenset({"guardado", "ya_existia", "mercado_abierto"})


@dataclass(frozen=True)
class Umbrales:
    horas_sin_ejecucion: int = 24
    # 36 y no 24: el snapshot se guarda sobre las 18:30 de Madrid y la vigilancia corre por
    # la mañana. Con 36 h, un único día sin snapshot ya se detecta al día siguiente.
    horas_sin_snapshot: int = 36
    caida_cobertura_puntos: float = 15.0
    dias_referencia: int = 7
    minimo_dias_referencia: int = 3


@dataclass(frozen=True)
class Comprobacion:
    nombre: str
    ok: bool
    detalle: str


def comprobar_cron(
    ejecuciones: Sequence[EjecucionCron], *, ahora: datetime, umbrales: Umbrales
) -> Comprobacion:
    """Condición 1: el cron ha terminado bien al menos una vez en la ventana."""
    desde = ahora - timedelta(hours=umbrales.horas_sin_ejecucion)
    recientes = [e for e in ejecuciones if e.iniciado_en >= desde]
    sanas = [e for e in recientes if e.snapshot_resultado in RESULTADOS_SANOS and not e.error]

    if sanas:
        return Comprobacion("cron", True, f"{len(sanas)} de {len(recientes)} ejecuciones sanas en {umbrales.horas_sin_ejecucion} h")
    if not recientes:
        return Comprobacion("cron", False, f"el cron no ha corrido ni una vez en {umbrales.horas_sin_ejecucion} h")
    ultima = recientes[-1]
    return Comprobacion(
        "cron", False,
        f"{len(recientes)} ejecuciones en {umbrales.horas_sin_ejecucion} h y ninguna sana; "
        f"la última: {ultima.snapshot_resultado} ({ultima.error or 'sin mensaje'})",
    )


def comprobar_snapshot(
    ejecuciones: Sequence[EjecucionCron], *, ahora: datetime, umbrales: Umbrales
) -> Comprobacion:
    """Condición 2: se ha guardado un snapshot de mercado hace poco.

    Existe porque la 1 no basta: los disparos de madrugada terminan bien aunque el de tarde
    lleve días fallando.
    """
    desde = ahora - timedelta(hours=umbrales.horas_sin_snapshot)
    guardados = [e for e in ejecuciones if e.snapshot_resultado == "guardado" and e.iniciado_en >= desde]
    if guardados:
        return Comprobacion("snapshot", True, f"último snapshot guardado: {guardados[-1].iniciado_en:%Y-%m-%d %H:%M} UTC")
    return Comprobacion("snapshot", False, f"ningún snapshot de mercado guardado en {umbrales.horas_sin_snapshot} h")


def comprobar_cobertura(
    serie: Sequence[consultas.CoberturaDia], *, umbrales: Umbrales
) -> Comprobacion:
    """Condición 3: la cobertura de emparejamiento no se ha desplomado.

    Es el aviso que pedía R27 del MVP: una caída brusca significa que algo cambió upstream
    (futbolfantasy tocó su HTML, un equipo cambió de slug...) y los datos se están degradando
    en silencio. Se compara el último día con la media de los anteriores, no con un número
    fijo: la cobertura "normal" es la que tenga tu liga, no la que yo imagine.
    """
    con_dato = [c for c in serie if c.porcentaje is not None]
    if not con_dato:
        return Comprobacion("cobertura", True, "sin datos de cobertura todavía")

    actual, previos = con_dato[-1], con_dato[:-1][-umbrales.dias_referencia:]
    if len(previos) < umbrales.minimo_dias_referencia:
        return Comprobacion(
            "cobertura", True,
            f"{actual.porcentaje:.0f}% el {actual.fecha}; datos insuficientes para comparar "
            f"({len(previos)} de {umbrales.minimo_dias_referencia} días mínimos)",
        )

    media = sum(c.porcentaje for c in previos) / len(previos)
    caida = media - actual.porcentaje
    detalle = (
        f"{actual.porcentaje:.0f}% el {actual.fecha} frente a una media de {media:.0f}% "
        f"en {len(previos)} días ({actual.sin_emparejar} jugadores sin emparejar)"
    )
    return Comprobacion("cobertura", caida <= umbrales.caida_cobertura_puntos, detalle)


def comprobar_logins(estados: Sequence[consultas.EstadoToken]) -> Comprobacion:
    """Condición 4: nadie tiene un login automático fallido sin recuperar.

    `ultimo_error` se escribe cuando un intento de renovar el token falla y se borra con el
    siguiente éxito. NO se usa la antigüedad de `ultimo_login_ok`: solo cambia al renovar, y
    el token de quien no abre la app no se renueva, aunque no haya nada roto.
    """
    fallidos = [e for e in estados if e.ultimo_error]
    if not fallidos:
        return Comprobacion("login", True, f"{len(estados)} cuentas con login automático, ninguna con error")
    # O20: id, nunca email. El admin lo cruza en /uso si necesita saber quién es.
    lista = "; ".join(f"usuario {e.user_id}: {e.ultimo_error}" for e in fallidos)
    return Comprobacion("login", False, f"{len(fallidos)} con login fallido — {lista}")


def evaluar(
    sesion: Session, *, ahora: datetime, umbrales: Umbrales = Umbrales()
) -> list[Comprobacion]:
    """Lee de la BD lo necesario y pasa todas las comprobaciones."""
    horas = max(umbrales.horas_sin_ejecucion, umbrales.horas_sin_snapshot)
    ejecuciones = consultas.ejecuciones_desde(sesion, desde=ahora - timedelta(hours=horas))
    serie = consultas.cobertura_diaria(
        sesion, hasta=fecha_local(ahora), dias=umbrales.dias_referencia * 2 + 1
    )
    return [
        comprobar_cron(ejecuciones, ahora=ahora, umbrales=umbrales),
        comprobar_snapshot(ejecuciones, ahora=ahora, umbrales=umbrales),
        comprobar_cobertura(serie, umbrales=umbrales),
        comprobar_logins(consultas.estado_tokens(sesion)),
    ]
