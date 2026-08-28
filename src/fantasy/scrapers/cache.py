"""Caché local en disco para no re-scrapear si no hace falta (ver design.md §Reglas de
scraping). Clave por URL, ventana de validez configurable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("fantasy.scrapers.cache")

DIR_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "cache"


def _ruta_para(url: str) -> Path:
    clave = hashlib.sha256(url.encode()).hexdigest()[:24]
    return DIR_CACHE / f"{clave}.json"


def leer(url: str, ventana_segundos: float) -> str | None:
    """Devuelve el HTML cacheado si está dentro de la ventana de validez, o None."""
    ruta = _ruta_para(url)
    if not ruta.exists():
        return None

    entrada = json.loads(ruta.read_text())
    edad = time.time() - entrada["guardado_en"]
    if edad > ventana_segundos:
        logger.info("caché caducada para %s (edad %.0fs > %.0fs)", url, edad, ventana_segundos)
        return None

    logger.info("caché HIT para %s (edad %.0fs)", url, edad)
    return entrada["html"]


def guardar(url: str, html: str) -> None:
    DIR_CACHE.mkdir(parents=True, exist_ok=True)
    ruta = _ruta_para(url)
    ruta.write_text(json.dumps({"url": url, "guardado_en": time.time(), "html": html}))
