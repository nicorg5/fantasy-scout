"""Modelos tipados de la API oficial, con validación explícita de forma.

Estructura verificada contra respuestas reales el 2026-08-26 (ver design.md §Fuente 1 y
`data/raw/`). Todo campo ausente o de tipo inesperado lanza `RespuestaInesperada`
nombrando su camino: nunca un `KeyError` pelado ni un `None` que se cuele hacia la UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fantasy.official.errores import RespuestaInesperada

# `discr` distingue dos poblaciones mezcladas en /market. Solo la primera entra en el MVP
# (decisión del usuario): los jugadores libres que saca el sistema.
DISCR_JUGADOR_LIBRE = "marketPlayerLeague"
DISCR_VENTA_DE_MANAGER = "marketPlayerTeam"

# `positionId` es el único campo de posición presente en AMBOS endpoints; el nombre
# legible (`position`) solo viene en mercado. Mapeo deducido de datos reales (2026-08-26).
POSICIONES = {1: "Portero", 2: "Defensa", 3: "Centrocampista", 4: "Delantero", 5: "Entrenador"}


def _exigir(datos: Any, clave: str, tipos: type | tuple[type, ...], camino: str) -> Any:
    if not isinstance(datos, dict):
        raise RespuestaInesperada(camino, f"se esperaba un objeto y llegó {type(datos).__name__}")
    if clave not in datos:
        raise RespuestaInesperada(f"{camino}.{clave}", "el campo no viene en la respuesta")
    valor = datos[clave]
    if not isinstance(valor, tipos):
        esperado = tipos.__name__ if isinstance(tipos, type) else "/".join(t.__name__ for t in tipos)
        raise RespuestaInesperada(
            f"{camino}.{clave}", f"se esperaba {esperado} y llegó {type(valor).__name__}"
        )
    return valor


def _equipo_id(datos: Any, camino: str) -> int:
    """Mercado usa `teamId` (int); plantilla, `team.id` (cadena). Normaliza a int."""
    if "teamId" in datos:
        return _exigir(datos, "teamId", int, camino)
    equipo = _exigir(datos, "team", dict, camino)
    crudo = _exigir(equipo, "id", (str, int), f"{camino}.team")
    try:
        return int(crudo)
    except (TypeError, ValueError) as exc:
        raise RespuestaInesperada(f"{camino}.team.id", f"no es un entero: {crudo!r}") from exc


def _posicion(datos: Any, camino: str) -> str:
    """`position` solo viene en mercado; `positionId` viene en ambos, así que manda ese."""
    id_posicion = _exigir(datos, "positionId", int, camino)
    if id_posicion not in POSICIONES:
        raise RespuestaInesperada(
            f"{camino}.positionId",
            f"posición desconocida: {id_posicion}. ¿La API añadió una nueva?",
        )
    return POSICIONES[id_posicion]


@dataclass(frozen=True)
class JugadorOficial:
    """`playerMaster`, presente en mercado y en plantilla.

    **Las dos formas NO son idénticas** (verificado 2026-08-26): mercado trae
    `teamId` (int) y `position` (texto); plantilla trae `team` (objeto, con `id` como
    **cadena**) y no trae `position`. Este parser normaliza ambas a la misma salida.
    """

    id: str
    nombre: str
    apodo: str
    slug: str
    equipo_id: int
    posicion: str
    valor_mercado: int
    estado: str
    puntos: int
    media_puntos: float

    @classmethod
    def desde_api(cls, datos: Any, camino: str = "playerMaster") -> JugadorOficial:
        return cls(
            # id es cadena a propósito: ver la trampa de tipos en design.md.
            id=str(_exigir(datos, "id", (str, int), camino)),
            nombre=_exigir(datos, "name", str, camino),
            apodo=_exigir(datos, "nickname", str, camino),
            slug=_exigir(datos, "slug", str, camino),
            equipo_id=_equipo_id(datos, camino),
            posicion=_posicion(datos, camino),
            valor_mercado=_exigir(datos, "marketValue", int, camino),
            # Dato OFICIAL de disponibilidad (ok/doubtful/injured/suspended/...).
            # NO es la probabilidad de jugar scrapeada: no se mezclan ni se sustituyen.
            estado=_exigir(datos, "playerStatus", str, camino),
            puntos=_exigir(datos, "points", int, camino),
            media_puntos=float(_exigir(datos, "averagePoints", (int, float), camino)),
        )


@dataclass(frozen=True)
class SubastaMercado:
    """Un jugador libre en subasta hoy (`discr == marketPlayerLeague`)."""

    id: str
    jugador: JugadorOficial
    precio_venta: int
    expira_en: datetime
    numero_pujas: int

    @classmethod
    def desde_api(cls, datos: Any, camino: str = "market[]") -> SubastaMercado:
        crudo_fecha = _exigir(datos, "expirationDate", str, camino)
        try:
            expira_en = datetime.fromisoformat(crudo_fecha)
        except ValueError as exc:
            raise RespuestaInesperada(
                f"{camino}.expirationDate", f"fecha no interpretable: {crudo_fecha!r}"
            ) from exc

        return cls(
            id=str(_exigir(datos, "id", (str, int), camino)),
            jugador=JugadorOficial.desde_api(
                _exigir(datos, "playerMaster", dict, camino), f"{camino}.playerMaster"
            ),
            precio_venta=_exigir(datos, "salePrice", int, camino),
            expira_en=expira_en,
            numero_pujas=_exigir(datos, "numberOfBids", int, camino),
        )


@dataclass(frozen=True)
class JugadorPlantilla:
    jugador: JugadorOficial
    clausula: int
    blindado: bool

    @classmethod
    def desde_api(cls, datos: Any, camino: str = "players[]") -> JugadorPlantilla:
        return cls(
            jugador=JugadorOficial.desde_api(
                _exigir(datos, "playerMaster", dict, camino), f"{camino}.playerMaster"
            ),
            clausula=_exigir(datos, "buyoutClause", int, camino),
            blindado=_exigir(datos, "isShielded", bool, camino),
        )


@dataclass(frozen=True)
class Plantilla:
    jugadores: list[JugadorPlantilla]
    dinero: int
    valor_equipo: int
    puntos: int

    @classmethod
    def desde_api(cls, datos: Any, camino: str = "plantilla") -> Plantilla:
        crudos = _exigir(datos, "players", list, camino)
        return cls(
            jugadores=[
                JugadorPlantilla.desde_api(j, f"{camino}.players[{i}]")
                for i, j in enumerate(crudos)
            ],
            dinero=_exigir(datos, "teamMoney", int, camino),
            valor_equipo=_exigir(datos, "teamValue", int, camino),
            puntos=_exigir(datos, "teamPoints", int, camino),
        )


def parsear_mercado(datos: Any, camino: str = "market") -> list[SubastaMercado]:
    """Filtra por `discr` **antes** de parsear.

    Las dos poblaciones tienen campos distintos (`marketPlayerTeam` no trae
    `numberOfBids`), así que parsear primero y filtrar después reventaría con los
    jugadores en venta de otros managers.
    """
    if not isinstance(datos, list):
        raise RespuestaInesperada(camino, f"se esperaba una lista y llegó {type(datos).__name__}")

    subastas = []
    for i, elemento in enumerate(datos):
        if not isinstance(elemento, dict):
            raise RespuestaInesperada(f"{camino}[{i}]", "se esperaba un objeto")
        if elemento.get("discr") != DISCR_JUGADOR_LIBRE:
            continue
        subastas.append(SubastaMercado.desde_api(elemento, f"{camino}[{i}]"))
    return subastas
