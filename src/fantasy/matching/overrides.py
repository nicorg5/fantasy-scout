"""Overrides manuales del matching: la válvula de escape cuando la heurística falla.

**Tienen precedencia absoluta** sobre el algoritmo. Viven versionados en
`data/mappings/overrides.json` — esa es la razón de que `data/mappings/` sí se versione
mientras `data/raw/` y `data/cache/` están gitignored (ver design.md).

Formato:

    {
      "3053": {
        "id_externo": "e119",
        "nota": "por qué se corrigió a mano, para que dentro de un año se entienda"
      }
    }
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("fantasy.matching.overrides")

RUTA_OVERRIDES = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "mappings" / "overrides.json"
)


def cargar_overrides() -> dict[str, str]:
    """Devuelve {id_oficial: id_externo}. Fichero ausente = sin overrides, no es un error."""
    if not RUTA_OVERRIDES.exists():
        return {}

    crudo = json.loads(RUTA_OVERRIDES.read_text())
    overrides = {
        id_oficial: entrada["id_externo"]
        for id_oficial, entrada in crudo.items()
        # Las claves que empiezan por '_' son documentación dentro del propio fichero
        # (JSON no admite comentarios), no overrides reales.
        if not id_oficial.startswith("_")
        and isinstance(entrada, dict)
        and "id_externo" in entrada
    }
    if overrides:
        logger.info("cargados %d overrides manuales de matching", len(overrides))
    return overrides
