"""Puente entre los IDs de equipo de la API oficial y los de futbolfantasy.

Los identificadores no coinciden y **no dan error al cruzarlos** (Málaga es 12 en oficial
y 11 en futbolfantasy, ambos enteros pequeños), así que un cruce directo emparejaría
equipos equivocados en silencio. Ver design.md §Cruce de IDs.

El mapa se genera con `scripts/construir_mapa_equipos.py` y se **versiona** en
`data/mappings/equipos.json`: es estable durante la temporada y no se recalcula sola.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

RUTA_MAPA = Path(__file__).resolve().parent.parent.parent.parent / "data" / "mappings" / "equipos.json"


class MapaEquiposAusente(Exception):
    """Sin el mapa no se puede acotar por equipo, y sin acotar el matching no es fiable."""


@lru_cache(maxsize=1)
def cargar_mapa_equipos() -> dict[str, dict[str, str]]:
    if not RUTA_MAPA.exists():
        raise MapaEquiposAusente(
            f"No existe {RUTA_MAPA}. Genéralo con: "
            "uv run python scripts/construir_mapa_equipos.py --escribir"
        )
    return json.loads(RUTA_MAPA.read_text())


def equipo_futbolfantasy(equipo_id_oficial: int | str) -> str | None:
    """Id numérico del equipo en futbolfantasy (el que usa `data-equipo` en la tabla de
    mercado), o None si ese equipo no está mapeado — por ejemplo un recién ascendido."""
    entrada = cargar_mapa_equipos().get(str(equipo_id_oficial))
    return entrada["id"] if entrada else None


def slug_equipo_futbolfantasy(equipo_id_oficial: int | str) -> str | None:
    """Slug del equipo, necesario para la URL de su página (`/laliga/equipos/malaga`).

    Es distinto del id numérico: la tabla de mercado identifica el equipo con un número
    y la URL con un slug. Usar el número en la URL devuelve 404 — pasó de verdad.
    """
    entrada = cargar_mapa_equipos().get(str(equipo_id_oficial))
    slug = entrada["slug"] if entrada else None
    return slug or None


def nombre_equipo(equipo_id_oficial: int | str) -> str | None:
    """Nombre legible del equipo ('Málaga'). Sin él, la UI mostraría el id numérico."""
    entrada = cargar_mapa_equipos().get(str(equipo_id_oficial))
    nombre = entrada["nombre"] if entrada else None
    return nombre or None
