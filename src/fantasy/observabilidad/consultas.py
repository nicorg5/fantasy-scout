"""Consultas de lectura sobre `evento_uso` y `ejecucion_cron` (specs/observabilidad, T9).

Las usan `/uso` (para mirar) y `scripts/vigilancia.py` (para avisar). Todas reciben el
momento de referencia (`ahora`) como parámetro en vez de leer el reloj: así los tests fijan
el tiempo y las dos pantallas que las usan ven exactamente lo mismo.

**Sesiones (design.md §D5)**: no se guardan, se derivan. Dos eventos seguidos de un mismo
usuario separados por más de `umbral` pertenecen a sesiones distintas. Cambiar el umbral
recalcula todo el histórico sin tocar un dato.

**Duración de sesión**: una sesión de un solo evento tiene duración *desconocida*, no 0: se
sabe cuándo llegó, no cuándo se fue. Por la invariante nº 2 del proyecto ("un dato ausente
jamás se disfraza de dato real"), esas sesiones se cuentan aparte y no entran en la mediana.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from fantasy.storage.fechas import MADRID
from fantasy.storage.modelos import (
    CredencialesLaLiga,
    EjecucionCron,
    EventoUso,
    TipoEvento,
    Usuario,
)

UMBRAL_SESION = timedelta(minutes=30)
_NUNCA = datetime.min.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------
# Sesiones
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SesionUso:
    user_id: uuid.UUID
    inicio: datetime
    fin: datetime
    eventos: int

    @property
    def duracion(self) -> timedelta | None:
        """`None` si solo hubo un evento: no se sabe cuánto duró (ver docstring del módulo)."""
        return self.fin - self.inicio if self.eventos > 1 else None


def _subconsulta_sesiones(desde: datetime, umbral: timedelta):
    """Tres niveles, porque una función de ventana no puede anidarse en otra:

    1. `anterior`: el evento previo del mismo usuario (LAG).
    2. `numero`: suma acumulada de "aquí empieza sesión nueva" → identifica cada sesión.
    3. agrupar por (usuario, número) → inicio, fin y nº de eventos.

    Las peticiones sin usuario (rebotes al login) no forman sesiones: no hay a quién
    atribuirlas. Una sesión que empezó antes de `desde` se ve cortada por ese borde.
    """
    anterior = (
        select(
            EventoUso.user_id,
            EventoUso.creado_en,
            func.lag(EventoUso.creado_en)
            .over(partition_by=EventoUso.user_id, order_by=EventoUso.creado_en)
            .label("anterior"),
        )
        .where(EventoUso.user_id.is_not(None), EventoUso.creado_en >= desde)
        .subquery()
    )

    empieza = case(
        (or_(anterior.c.anterior.is_(None), anterior.c.creado_en - anterior.c.anterior > umbral), 1),
        else_=0,
    )
    numerados = select(
        anterior.c.user_id,
        anterior.c.creado_en,
        func.sum(empieza)
        .over(partition_by=anterior.c.user_id, order_by=anterior.c.creado_en)
        .label("numero"),
    ).subquery()

    return (
        select(
            numerados.c.user_id,
            func.min(numerados.c.creado_en).label("inicio"),
            func.max(numerados.c.creado_en).label("fin"),
            func.count().label("eventos"),
        )
        .group_by(numerados.c.user_id, numerados.c.numero)
        .subquery()
    )


def sesiones(
    sesion: Session, *, desde: datetime, umbral: timedelta = UMBRAL_SESION
) -> list[SesionUso]:
    sub = _subconsulta_sesiones(desde, umbral)
    filas = sesion.execute(select(sub).order_by(sub.c.inicio))
    return [SesionUso(f.user_id, f.inicio, f.fin, f.eventos) for f in filas]


@dataclass(frozen=True)
class ResumenSesiones:
    total: int
    # Sesiones de una sola petición: duración desconocida, NO cero.
    de_una_pagina: int
    mediana: timedelta | None
    p95: timedelta | None


def resumen_sesiones(
    sesion: Session, *, desde: datetime, umbral: timedelta = UMBRAL_SESION
) -> ResumenSesiones:
    sub = _subconsulta_sesiones(desde, umbral)
    duracion = sub.c.fin - sub.c.inicio
    medibles = sub.c.eventos > 1

    fila = sesion.execute(
        select(
            func.count(),
            func.count().filter(~medibles),
            func.percentile_cont(0.5).within_group(duracion).filter(medibles),
            func.percentile_cont(0.95).within_group(duracion).filter(medibles),
        ).select_from(sub)
    ).one()
    return ResumenSesiones(total=fila[0], de_una_pagina=fila[1], mediana=fila[2], p95=fila[3])


# --------------------------------------------------------------------------------------
# Usuarios y secciones
# --------------------------------------------------------------------------------------


def inicio_del_dia(momento: datetime) -> datetime:
    """Medianoche de Madrid del día de `momento`: "hoy" es el día de Madrid, no el UTC."""
    local = momento.astimezone(MADRID)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class UsuariosActivos:
    hoy: int
    ultimos_7_dias: int
    ultimos_30_dias: int


def usuarios_activos(sesion: Session, *, ahora: datetime) -> UsuariosActivos:
    """Personas distintas que hicieron algo. Los días se cuentan en hora de Madrid."""
    hoy = inicio_del_dia(ahora)

    def _distintos(desde: datetime):
        return func.count(func.distinct(EventoUso.user_id)).filter(EventoUso.creado_en >= desde)

    fila = sesion.execute(
        select(
            _distintos(hoy),
            _distintos(hoy - timedelta(days=6)),
            _distintos(hoy - timedelta(days=29)),
        ).where(EventoUso.user_id.is_not(None), EventoUso.creado_en >= hoy - timedelta(days=29))
    ).one()
    return UsuariosActivos(*fila)


@dataclass(frozen=True)
class UsoDeRuta:
    ruta: str
    tipo: TipoEvento
    accion: str | None
    veces: int


def uso_por_ruta(sesion: Session, *, desde: datetime) -> list[UsoDeRuta]:
    """Cuántas veces se abrió cada pantalla y se pulsó cada acción. Solo usuarios con
    sesión: los rebotes al login no son "clicar en una sección"."""
    filas = sesion.execute(
        select(EventoUso.ruta, EventoUso.tipo, EventoUso.accion, func.count().label("veces"))
        .where(EventoUso.creado_en >= desde, EventoUso.user_id.is_not(None))
        .group_by(EventoUso.ruta, EventoUso.tipo, EventoUso.accion)
        .order_by(func.count().desc(), EventoUso.ruta)
    )
    return [UsoDeRuta(*f) for f in filas]


@dataclass(frozen=True)
class DetalleUsuario:
    user_id: uuid.UUID
    email: str
    ultima_visita: datetime | None
    sesiones: int
    seccion_favorita: str | None


def detalle_por_usuario(
    sesion: Session, *, desde: datetime, umbral: timedelta = UMBRAL_SESION
) -> list[DetalleUsuario]:
    """Una fila por cuenta, **también las que no han entrado** en el periodo: que alguien no
    aparezca nunca es información, y ocultarlo la escondería."""
    sub = _subconsulta_sesiones(desde, umbral)
    n_sesiones = dict(
        sesion.execute(select(sub.c.user_id, func.count()).group_by(sub.c.user_id)).all()
    )

    ultima = dict(
        sesion.execute(
            select(EventoUso.user_id, func.max(EventoUso.creado_en))
            .where(EventoUso.user_id.is_not(None))
            .group_by(EventoUso.user_id)
        ).all()
    )

    # Sección más abierta en el periodo (navegaciones, no acciones). Empate: la primera
    # por orden alfabético, para que el resultado sea estable entre cargas.
    ranking = (
        select(
            EventoUso.user_id,
            EventoUso.ruta,
            func.row_number()
            .over(
                partition_by=EventoUso.user_id,
                order_by=(func.count().desc(), EventoUso.ruta),
            )
            .label("puesto"),
        )
        .where(
            EventoUso.user_id.is_not(None),
            EventoUso.creado_en >= desde,
            EventoUso.tipo == TipoEvento.NAVEGACION,
        )
        .group_by(EventoUso.user_id, EventoUso.ruta)
        .subquery()
    )
    favorita = dict(
        sesion.execute(select(ranking.c.user_id, ranking.c.ruta).where(ranking.c.puesto == 1)).all()
    )

    usuarios = sesion.execute(select(Usuario.id, Usuario.email).order_by(Usuario.email)).all()
    detalle = [
        DetalleUsuario(
            user_id=uid,
            email=email,
            ultima_visita=ultima.get(uid),
            sesiones=n_sesiones.get(uid, 0),
            seccion_favorita=favorita.get(uid),
        )
        for uid, email in usuarios
    ]
    # Los más recientes arriba; quien nunca entró, al final.
    return sorted(detalle, key=lambda d: d.ultima_visita or _NUNCA, reverse=True)


# --------------------------------------------------------------------------------------
# Salud técnica
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LatenciaDeRuta:
    ruta: str
    peticiones: int
    p50_ms: float
    p95_ms: float
    errores_5xx: int


def latencia_por_ruta(sesion: Session, *, desde: datetime) -> list[LatenciaDeRuta]:
    """Percentiles, nunca media: una media esconde al 5% que espera una eternidad.

    Aviso para leerlo: el plan gratuito de Render duerme el servicio y la primera petición
    tras dormir tarda decenas de segundos. Si el p95 se dispara y el p50 no, suele ser eso.
    """
    filas = sesion.execute(
        select(
            EventoUso.ruta,
            func.count(),
            func.percentile_cont(0.5).within_group(EventoUso.duracion_ms),
            func.percentile_cont(0.95).within_group(EventoUso.duracion_ms),
            func.count().filter(EventoUso.estado_http >= 500),
        )
        .where(EventoUso.creado_en >= desde)
        .group_by(EventoUso.ruta)
        .order_by(EventoUso.ruta)
    )
    return [LatenciaDeRuta(*f) for f in filas]


@dataclass(frozen=True)
class EstadoToken:
    user_id: uuid.UUID
    email: str
    ultimo_login_ok: datetime | None
    ultimo_error: str | None


def estado_tokens(sesion: Session) -> list[EstadoToken]:
    """Solo cuentas con login automático: sin credenciales guardadas no hay nada que vigilar."""
    filas = sesion.execute(
        select(
            Usuario.id, Usuario.email, CredencialesLaLiga.ultimo_login_ok, CredencialesLaLiga.ultimo_error
        )
        .join(CredencialesLaLiga, CredencialesLaLiga.user_id == Usuario.id)
        .order_by(Usuario.email)
    )
    return [EstadoToken(*f) for f in filas]


def ultimas_ejecuciones(sesion: Session, *, limite: int = 12) -> list[EjecucionCron]:
    return list(
        sesion.scalars(select(EjecucionCron).order_by(EjecucionCron.iniciado_en.desc()).limit(limite))
    )


@dataclass(frozen=True)
class CoberturaDia:
    fecha: date
    jugadores: int
    con_analitica: int
    sin_emparejar: int | None

    @property
    def porcentaje(self) -> float | None:
        """`None` si no hubo jugadores: 0/0 no es 0%, es "sin dato"."""
        return 100 * self.con_analitica / self.jugadores if self.jugadores else None


def cobertura_diaria(sesion: Session, *, hasta: date, dias: int = 14) -> list[CoberturaDia]:
    """O12: cobertura de emparejamiento por día (jugadores con analítica / jugadores).

    Solo cuentan las ejecuciones que **guardaron** el snapshot: son las únicas que
    emparejan contra el mercado oficial (1 de cada 4 disparos del cron). El resto deja estos
    contadores a NULL y promediarlas como 0 hundiría la cobertura de forma ficticia.

    Si un día hubo varias (no debería: el snapshot es idempotente), vale la última.
    """
    fecha_madrid = func.date(func.timezone("Europe/Madrid", EjecucionCron.iniciado_en))
    desde = hasta - timedelta(days=dias - 1)

    ultima_del_dia = (
        select(
            fecha_madrid.label("fecha"),
            EjecucionCron.jugadores_escritos,
            EjecucionCron.con_analitica,
            EjecucionCron.sin_emparejar,
            func.row_number()
            .over(partition_by=fecha_madrid, order_by=EjecucionCron.iniciado_en.desc())
            .label("puesto"),
        )
        .where(
            EjecucionCron.snapshot_resultado == "guardado",
            and_(fecha_madrid >= desde, fecha_madrid <= hasta),
        )
        .subquery()
    )
    filas = sesion.execute(
        select(
            ultima_del_dia.c.fecha,
            ultima_del_dia.c.jugadores_escritos,
            ultima_del_dia.c.con_analitica,
            ultima_del_dia.c.sin_emparejar,
        )
        .where(ultima_del_dia.c.puesto == 1)
        .order_by(ultima_del_dia.c.fecha)
    )
    return [CoberturaDia(f.fecha, f.jugadores_escritos or 0, f.con_analitica or 0, f.sin_emparejar) for f in filas]


def ejecuciones_desde(sesion: Session, *, desde: datetime) -> list[EjecucionCron]:
    return list(
        sesion.scalars(
            select(EjecucionCron)
            .where(EjecucionCron.iniciado_en >= desde)
            .order_by(EjecucionCron.iniciado_en)
        )
    )
