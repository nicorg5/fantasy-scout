"""Middleware que registra el uso de la app (design.md §D1, §D3).

**Por qué un middleware y no un decorador por ruta**: hay ocho rutas y habrá más. Con un
decorador, el día que se olvide en la novena pantalla no falla nada — esa pantalla
simplemente desaparece de las métricas, en silencio. Es el peor modo de fallo posible en
observabilidad, porque lleva a conclusiones falsas creyendo que se tienen los datos.

Además es el único sitio que ve el `302` que produce el handler de `NoAutenticado` (O9) y
que puede medir cuánto tardó la respuesta (O8).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import Request
from fastapi.responses import Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from fantasy.auth.sesiones import NOMBRE_COOKIE, leer_usuario_id_de_cookie
from fantasy.observabilidad.registro import (
    RUTA_DESCONOCIDA,
    debe_registrarse,
    registrar_evento,
)


def _plantilla_de_ruta(request: Request) -> str:
    """La ruta *con sus parámetros sin sustituir*, nunca la URL real (design.md §D4).

    Un 404 no tiene ruta asociada, y ahí no hay nada que agrupar.
    """
    ruta = request.scope.get("route")
    return getattr(ruta, "path", None) or RUTA_DESCONOCIDA


async def registrar_uso(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if not debe_registrarse(request.url.path):
        return await call_next(request)

    inicio = perf_counter()
    respuesta = await call_next(request)
    duracion_ms = int((perf_counter() - inicio) * 1000)

    # El id sale de la cookie ya verificada criptográficamente: no hace falta consultar la
    # tabla `user` para saber quién es (design.md §D2). Sin cookie válida queda `None`, que
    # es exactamente lo que O9 pide.
    usuario_id = leer_usuario_id_de_cookie(request.cookies.get(NOMBRE_COOKIE))

    # Se respeta cualquier tarea que la ruta hubiera adjuntado ya. Hoy ninguna lo hace,
    # pero sobrescribirla sin más sería un bug silencioso el día que alguna lo haga.
    tarea_previa = respuesta.background

    async def _registrar() -> None:
        if tarea_previa is not None:
            await tarea_previa()
        # `registrar_evento` es síncrono (psycopg bloqueante). Llamarlo directamente desde
        # esta corrutina ejecutaría el INSERT EN el event loop y frenaría a todas las
        # demás peticiones mientras dura: medido en local, +5 ms en cada respuesta siguiente.
        # En el threadpool, el loop sigue libre.
        await run_in_threadpool(
            registrar_evento,
            ruta=_plantilla_de_ruta(request),
            estado_http=respuesta.status_code,
            duracion_ms=duracion_ms,
            usuario_id=usuario_id,
            tiene_query=bool(request.url.query),
        )

    # La clave de todo el diseño: Starlette ejecuta esto DESPUÉS de enviar la respuesta, así
    # que el usuario no espera a que se escriba en Neon (O7). Verificado en T2.
    respuesta.background = BackgroundTask(_registrar)
    return respuesta
