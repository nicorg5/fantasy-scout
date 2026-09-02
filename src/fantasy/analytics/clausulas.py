"""Pantalla de clausulazos: quien tiene a cada jugador, cuanto cuesta clausularlo y
cuando se puede.

A diferencia de plantilla y mercado, esto necesita leer las plantillas de **todos** los
managers (1 + N peticiones a la API oficial, ~3,6 s con 10 managers). Es el unico sitio
del proyecto que lo hace, y solo para esta pantalla.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from fantasy.analytics.presentacion import BloqueAnalitico, ClausulaPresentada
from fantasy.analytics.servicio import MOTIVO_SCRAPING_CAIDO, recolectar_analitica_de_bd
from fantasy.matching.analitica import construir_bloque_analitico
from fantasy.matching.equipos import nombre_equipo
from fantasy.official.cliente import ClienteOficial

logger = logging.getLogger("fantasy.analytics.clausulas")

ORDENES = ("desbloqueo", "clausula_asc", "clausula_desc", "sobrepago_asc", "sobrepago_desc")
ESTADOS = ("disponibles", "bloqueados")


@dataclass(frozen=True)
class FiltrosClausulas:
    """Filtros de la pantalla. Todos opcionales; vacio = sin filtrar."""

    manager: str | None = None
    posicion: str | None = None
    equipo: str | None = None
    # None = todos. "disponibles" = fichables ya. "bloqueados" = el resto, incluidos los
    # blindados: para el usuario son lo mismo, jugadores que hoy no puede clausular.
    estado: str | None = None
    orden: str = "desbloqueo"

    @classmethod
    def desde_query(
        cls, manager=None, posicion=None, equipo=None, orden=None, estado=None
    ) -> FiltrosClausulas:
        """Normaliza lo que llega por query string: cadenas vacias cuentan como 'sin filtro'."""
        def limpio(v):
            v = (v or "").strip()
            return v or None

        pedido = limpio(orden) or "desbloqueo"
        estado_pedido = limpio(estado)
        return cls(
            manager=limpio(manager),
            posicion=limpio(posicion),
            equipo=limpio(equipo),
            # Igual que con el orden: un valor inventado en la URL no rompe la pagina,
            # simplemente no filtra.
            estado=estado_pedido if estado_pedido in ESTADOS else None,
            orden=pedido if pedido in ORDENES else "desbloqueo",
        )


def _clave_de_orden(fila: ClausulaPresentada, orden: str):
    """Los FICHABLES van primero siempre (decision del usuario), y dentro de cada grupo
    manda el criterio elegido. El primer elemento de la tupla es el que agrupa."""
    grupo = 0 if fila.fichable else 1

    if orden == "clausula_asc":
        return (grupo, fila.clausula_euros)
    if orden == "clausula_desc":
        return (grupo, -fila.clausula_euros)
    if orden == "sobrepago_asc":
        return (grupo, fila.sobrepago_euros)
    if orden == "sobrepago_desc":
        return (grupo, -fila.sobrepago_euros)

    # Por defecto: los que antes se liberan primero. Los ya disponibles no tienen espera,
    # asi que se ordenan por clausula para que la lista siga siendo util.
    if fila.segundos_para_desbloqueo is None:
        return (grupo, fila.clausula_euros)
    return (grupo, fila.segundos_para_desbloqueo)


def obtener_clausulas(
    sesion: Session, usuario_id: uuid.UUID, filtros: FiltrosClausulas | None = None
) -> tuple[list[ClausulaPresentada], dict[str, list[str]]]:
    """Devuelve (filas ya ordenadas y filtradas, opciones disponibles para los desplegables).

    Las opciones se calculan sobre TODAS las filas, no sobre las filtradas: si se
    calcularan despues, al elegir un manager desapareceria el resto del desplegable y no
    se podria volver atras.
    """
    filtros = filtros or FiltrosClausulas()
    ahora = datetime.now(timezone.utc)

    cliente = ClienteOficial(sesion, usuario_id)
    league_id, team_id = cliente.obtener_liga_y_equipo()
    # Se excluye el equipo propio: clausularse a uno mismo no tiene sentido.
    con_clausula = cliente.obtener_clausulas_de_la_liga(league_id, excluir_team_id=team_id)

    oficiales = [c.jugador for c in con_clausula]
    analitica = recolectar_analitica_de_bd(sesion, oficiales)

    filas = []
    for item in con_clausula:
        if analitica.hay_datos:
            bloque = construir_bloque_analitico(
                item.jugador.id,
                analitica.emparejamientos,
                analitica.tendencias,
                analitica.probabilidades,
                posicion=item.jugador.posicion,
            )
        else:
            bloque = BloqueAnalitico.no_disponible(MOTIVO_SCRAPING_CAIDO)

        filas.append(
            ClausulaPresentada(
                id_oficial=item.jugador.id,
                nombre=item.jugador.apodo or item.jugador.nombre,
                manager=item.manager,
                equipo=nombre_equipo(item.jugador.equipo_id) or f"equipo {item.jugador.equipo_id}",
                posicion=item.jugador.posicion,
                valor_mercado_euros=item.jugador.valor_mercado,
                clausula_euros=item.clausula,
                sobrepago_euros=item.sobrepago_euros,
                sobrepago_pct=item.sobrepago_pct,
                segundos_para_desbloqueo=item.segundos_para_desbloqueo(ahora),
                blindado=item.blindado,
                analitica=bloque,
            )
        )

    opciones = {
        "managers": sorted({f.manager for f in filas}),
        "posiciones": sorted({f.posicion for f in filas}),
        "equipos": sorted({f.equipo for f in filas}),
    }

    if filtros.manager:
        filas = [f for f in filas if f.manager == filtros.manager]
    if filtros.posicion:
        filas = [f for f in filas if f.posicion == filtros.posicion]
    if filtros.equipo:
        filas = [f for f in filas if f.equipo == filtros.equipo]
    if filtros.estado == "disponibles":
        filas = [f for f in filas if f.fichable]
    elif filtros.estado == "bloqueados":
        filas = [f for f in filas if not f.fichable]

    filas.sort(key=lambda f: _clave_de_orden(f, filtros.orden))
    logger.info(
        "clausulas: %d filas (%d fichables ahora)",
        len(filas), sum(1 for f in filas if f.fichable),
    )
    return filas, opciones
