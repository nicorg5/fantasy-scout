"""Comprobación de robots.txt antes de tocar cualquier ruta nueva (regla no negociable,
ver design.md §Reglas de scraping). Se verifica por ruta, no una vez por dominio.
"""

from __future__ import annotations

import logging
import urllib.robotparser
from urllib.parse import urlparse

logger = logging.getLogger("fantasy.scrapers.robots")

_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


class RutaNoPermitida(Exception):
    """robots.txt prohíbe esta ruta para nuestro User-Agent."""


def _parser_para(origen: str, user_agent: str) -> urllib.robotparser.RobotFileParser:
    if origen not in _cache:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(f"{origen}/robots.txt")
        parser.read()
        _cache[origen] = parser
        logger.info("robots.txt leído de %s/robots.txt", origen)
    return _cache[origen]


def verificar_permiso(url: str, user_agent: str) -> None:
    """Lanza `RutaNoPermitida` si robots.txt prohíbe esta URL. Registra la comprobación
    siempre, se apruebe o no — es la evidencia de que se comprobó (ver R18)."""
    partes = urlparse(url)
    origen = f"{partes.scheme}://{partes.netloc}"
    parser = _parser_para(origen, user_agent)

    permitido = parser.can_fetch(user_agent, url)
    logger.info("robots.txt: %s -> %s (%s)", url, "permitido" if permitido else "PROHIBIDO", user_agent)

    if not permitido:
        raise RutaNoPermitida(f"robots.txt de {origen} prohíbe {url} para '{user_agent}'")
