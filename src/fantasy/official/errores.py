"""Errores de dominio del cliente de la API oficial.

La API no está documentada y puede cambiar sin aviso (ver design.md §Fuente 1). Por eso
un cambio de forma tiene que fallar **ruidosamente y nombrando el campo**, nunca degradar
en un `None` silencioso que se propague hasta la UI como un hueco inexplicable.
"""

from __future__ import annotations


class ErrorAPIOficial(Exception):
    """Base de todos los fallos hablando con la API oficial de LaLiga Fantasy."""


class TokenInvalido(ErrorAPIOficial):
    """LaLiga respondió 401: el token caducó o no vale.

    Es un error *esperado* y recurrente — el bearer dura 24h. La UI lo traduce en
    "vuelve a pegar tu token" (R13), nunca en un 500.
    """


class RespuestaInesperada(ErrorAPIOficial):
    """La respuesta no tiene la forma que esperábamos: la API cambió, o cambió el contrato.

    Lleva el camino del campo que falla para que el diagnóstico no requiera adivinar.
    """

    def __init__(self, camino: str, detalle: str) -> None:
        self.camino = camino
        self.detalle = detalle
        super().__init__(
            f"La API oficial devolvió algo inesperado en '{camino}': {detalle}. "
            f"Puede que el endpoint haya cambiado; revisa data/raw/ y design.md §Fuente 1."
        )
