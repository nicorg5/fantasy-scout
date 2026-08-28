"""Normalización de nombres para el cruce de IDs.

Aislada en su propio módulo porque es el corazón del matching y conviene poder probarla
y cambiarla sin tocar nada más (ver design.md §Cruce de IDs).
"""

from __future__ import annotations

import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios colapsados.

    'Álvaro Núñez' -> 'alvaro nunez'
    'Sánchez Flores' -> 'sanchez flores'

    Los acentos se quitan porque los dos lados no los escriben igual (`Álvaro Núñez`
    oficial vs. `alvaro nuñez` scrapeado, con la ñ conservada en un lado y no en el otro).
    """
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    solo_alfanumerico = re.sub(r"[^a-z0-9 ]", " ", sin_acentos)
    return " ".join(solo_alfanumerico.split())


def variantes_oficiales(nombre: str, apodo: str, slug: str) -> set[str]:
    """Las tres formas en que la API oficial nombra al mismo jugador.

    Hacen falta las tres porque ninguna gana siempre: `Larrubia` solo casa por `name`
    (`David Larrubia`), mientras `Llorenç` solo casa por `slug` (`llorenc-serred`).
    """
    return {
        normalizar(nombre),
        normalizar(apodo),
        normalizar(slug.replace("-", " ")),
    }
