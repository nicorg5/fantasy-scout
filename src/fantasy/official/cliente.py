"""Cliente de la API oficial de LaLiga Fantasy.

Contrato de seguridad (R17): el token **no se recibe desde la capa de presentación**. El
cliente lo obtiene descifrado del store a partir del id de usuario, lo usa en memoria para
la llamada saliente, y no lo devuelve ni lo registra en ningún log.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.orm import Session

from fantasy.auth.credenciales_store import obtener_token_valido
from fantasy.official.errores import RespuestaInesperada, TokenInvalido
from fantasy.official.modelos import Plantilla, SubastaMercado, parsear_mercado

HOST = "https://fantasy-api.llt-services.com"
COMPETICION = 1
TIMEOUT_SEGUNDOS = 20.0


class ClienteOficial:
    def __init__(self, sesion: Session, usuario_id: uuid.UUID) -> None:
        self._sesion = sesion
        self._usuario_id = usuario_id

    def _cabeceras(self) -> dict[str, str]:
        # Renueva solo si hace falta: token guardado -> refresh -> login completo.
        token = obtener_token_valido(self._sesion, self._usuario_id)
        if token is None:
            # Ni token válido ni forma de obtenerlo. Para el usuario la acción es la
            # misma que ante un 401: revisar sus credenciales o pegar un token.
            raise TokenInvalido(
                "No hay un token válido y no se ha podido renovar automáticamente."
            )
        return {
            "Authorization": f"Bearer {token}",
            # `x-app: 2` es obligatoria y no adivinable: ver design.md §Fuente 1.
            "x-app": "2",
            "x-lang": "es",
            "content-type": "application/json",
        }

    def _get(self, ruta: str) -> object:
        url = f"{HOST}{ruta}"
        try:
            with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as cliente:
                respuesta = cliente.get(url, headers=self._cabeceras(), params={"x-lang": "es"})
        except httpx.HTTPError as exc:
            raise RespuestaInesperada(ruta, f"fallo de red hablando con LaLiga: {exc}") from exc

        if respuesta.status_code == 401:
            raise TokenInvalido("LaLiga rechazó el token (401).")
        if respuesta.status_code != 200:
            raise RespuestaInesperada(ruta, f"HTTP {respuesta.status_code}")

        try:
            return respuesta.json()
        except ValueError as exc:
            raise RespuestaInesperada(ruta, "la respuesta no era JSON") from exc

    def obtener_mercado(self, league_id: str) -> list[SubastaMercado]:
        """Subastas de jugadores libres de hoy. `league_id` es CADENA (puede llevar
        ceros a la izquierda); convertirlo a int rompería la ruta."""
        datos = self._get(f"/api/v1/competition/{COMPETICION}/league/{league_id}/market")
        return parsear_mercado(datos)

    def obtener_plantilla(self, league_id: str, team_id: int) -> Plantilla:
        """Ojo: aquí la ruta usa `leagues` en plural, mientras el mercado usa `league`
        en singular. Es una inconsistencia real de la API, no una errata."""
        datos = self._get(
            f"/api/v1/competition/{COMPETICION}/leagues/{league_id}/teams/{team_id}"
        )
        return Plantilla.desde_api(datos)


    def obtener_liga_y_equipo(self) -> tuple[str, int]:
        """Devuelve (league_id, team_id) de la primera liga del usuario.

        Se consulta en vez de guardarse en nuestra base de datos: es una respuesta de 1 KB,
        evita duplicar estado que habría que mantener sincronizado, y si el usuario cambia
        de liga funciona solo. El MVP asume una liga por usuario (ver design.md §Alcance).
        """
        datos = self._get(f"/api/v1/competition/{COMPETICION}/leagues")
        ligas = datos if isinstance(datos, list) else datos.get("data", [])
        if not ligas:
            raise RespuestaInesperada("leagues", "el usuario no tiene ninguna liga")

        liga = ligas[0]
        equipo = liga.get("team") or {}
        if "id" not in liga or "id" not in equipo:
            raise RespuestaInesperada("leagues[0]", "falta el id de liga o de equipo")

        # El id de liga es CADENA (puede llevar ceros a la izquierda); el de equipo, entero.
        return str(liga["id"]), int(equipo["id"])
