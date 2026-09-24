"""Único punto de emisión de eventos de uso (O22, design.md §D3).

**Nadie más construye un `EventoUso`.** Si algún día hay que mandar los eventos también a
otro sitio —PostHog, un fichero, lo que sea—, se añade aquí y no se toca nada más. Ese es
el motivo de que esta función exista en vez de insertar desde el middleware: la
verificación de O22 es literalmente `grep -rn "EventoUso(" src/` devolviendo un resultado.

Dos reglas duras de este módulo:

1. **Nunca propaga una excepción.** Perder telemetría es un fastidio; tumbar una respuesta
   por no poder registrarla es un bug grave. Es la misma regla que ya aplica `scrapers/`.
2. **Nunca guarda la URL cruda.** Solo la plantilla de ruta, que es lo que le llega
   (design.md §D4).
"""

from __future__ import annotations

import logging
import uuid

from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import EventoUso, TipoEvento

logger = logging.getLogger("fantasy.observabilidad")

# Rutas que no son uso de la app y solo ensuciarían las métricas.
#
# `/uso` se excluye porque el panel vive DENTRO de la propia app (design.md §D8): sin esta
# línea, el administrador mirando las métricas aparecería como actividad de usuario en las
# métricas que está mirando.
RUTAS_IGNORADAS = frozenset({"/health", "/favicon.ico", "/uso"})
PREFIJOS_IGNORADOS = ("/static",)

# Conjunto CERRADO de acciones (O4). Lo que no esté aquí es navegación.
ACCION_ACTUALIZAR_DATOS = "actualizar_datos"
ACCION_FILTRAR_CLAUSULAS = "filtrar_clausulas"

# El botón "Actualizar datos" es la única razón de existir de estas dos rutas: devuelven
# el fragmento de tabla tras scrapear en vivo. Por eso la acción se deduce de la ruta y no
# hace falta tocar ni una plantilla (design.md §D6).
RUTAS_DE_REFRESCO = frozenset({"/plantilla/tabla", "/mercado/tabla"})

RUTA_DESCONOCIDA = "(desconocida)"


def debe_registrarse(ruta: str) -> bool:
    """Si esa ruta cuenta como uso de la app."""
    if ruta in RUTAS_IGNORADAS:
        return False
    return not ruta.startswith(PREFIJOS_IGNORADOS)


def clasificar(ruta: str, *, tiene_query: bool) -> tuple[TipoEvento, str | None]:
    """Traduce una petición a (tipo, acción). Función pura: se testea sin app ni BD.

    `/clausulas` con query string significa que alguien aplicó un filtro; sin ella, es una
    visita normal a la pantalla. No hay forma de confundirlas porque los filtros viajan
    siempre por query (ver `FiltrosClausulas.desde_query`).
    """
    if ruta in RUTAS_DE_REFRESCO:
        return TipoEvento.ACCION, ACCION_ACTUALIZAR_DATOS
    if ruta == "/clausulas" and tiene_query:
        return TipoEvento.ACCION, ACCION_FILTRAR_CLAUSULAS
    return TipoEvento.NAVEGACION, None


def registrar_evento(
    *,
    ruta: str,
    estado_http: int,
    duracion_ms: int,
    usuario_id: uuid.UUID | None = None,
    tiene_query: bool = False,
) -> None:
    """Guarda un evento de uso. No devuelve nada y no falla nunca.

    Abre **su propia** sesión de base de datos: cuando esto corre, la sesión de la petición
    ya está cerrada (ver `obtener_sesion`, que cierra en su `finally`).
    """
    tipo, accion = clasificar(ruta, tiene_query=tiene_query)

    try:
        fabrica = obtener_fabrica_sesiones()
        with fabrica() as sesion:
            sesion.add(
                EventoUso(
                    user_id=usuario_id,
                    ruta=ruta,
                    tipo=tipo,
                    accion=accion,
                    duracion_ms=duracion_ms,
                    estado_http=estado_http,
                )
            )
            sesion.commit()
    except Exception as exc:  # noqa: BLE001 - deliberado: ver regla 1 del módulo
        # `warning` y no `error`: la app sigue funcionando perfectamente sin esto.
        logger.warning("no se pudo registrar el evento de uso de %s: %s", ruta, exc)
