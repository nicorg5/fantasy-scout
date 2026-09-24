"""Registro de las ejecuciones del cron diario (specs/observabilidad, D7, O10-O13).

El script `snapshot_diario.py` va rellenando un `ResultadoEjecucion` a medida que avanza y
lo entrega aquí **siempre**, en un `finally`: las ejecuciones que fallan son justo las que
importan, y si esto fuera la última línea de `main()` no dejarían rastro.

Regla dura, la misma que `observabilidad/registro.py`: **registrar una ejecución nunca puede
romper el snapshot** (O13). Perder la fila es un fastidio; no guardar el mercado por no
poder anotarlo sería absurdo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from fantasy.storage.fechas import ahora_en_madrid
from fantasy.storage.modelos import RESULTADOS_SNAPSHOT, EjecucionCron

logger = logging.getLogger("fantasy.storage.ejecucion_repo")

_MAX_ERROR = 255


@dataclass
class ResultadoEjecucion:
    """Lo que el script va averiguando durante una ejecución.

    **El resultado por defecto es `error`, a propósito**: si algún camino del script se
    olvida de fijarlo, la fila dirá "error" y alguien lo mirará, en vez de decir "guardado"
    sin que sea verdad. Un fallo de instrumentación debe ser ruidoso, no optimista.
    """

    iniciado_en: datetime
    # Nulo (no 0) si el scraping cayó y no se guardó nada: son cosas distintas y la
    # invariante nº 2 del proyecto prohíbe disfrazar un dato ausente de cero.
    analitica_guardados: int | None = None
    snapshot_resultado: str = "error"
    jugadores_escritos: int | None = None
    con_analitica: int | None = None
    sin_emparejar: int | None = None
    error: str | None = None


def registrar_ejecucion(
    fabrica: sessionmaker[Session], resultado: ResultadoEjecucion
) -> None:
    """Guarda la fila de la ejecución. No devuelve nada y no falla nunca.

    Abre **su propia** sesión: si la ejecución murió a mitad de una transacción, la sesión
    del script puede estar en estado fallido y no admitir más escrituras.
    """
    try:
        estado = resultado.snapshot_resultado
        if estado not in RESULTADOS_SNAPSHOT:
            # Un valor desconocido no se descarta ni se maquilla: se anota como error y se
            # deja constancia de cuál era.
            logger.error("snapshot_resultado desconocido: %r", estado)
            estado = "error"

        with fabrica() as sesion:
            sesion.add(
                EjecucionCron(
                    iniciado_en=resultado.iniciado_en,
                    terminado_en=ahora_en_madrid(),
                    analitica_guardados=resultado.analitica_guardados,
                    snapshot_resultado=estado,
                    jugadores_escritos=resultado.jugadores_escritos,
                    con_analitica=resultado.con_analitica,
                    sin_emparejar=resultado.sin_emparejar,
                    error=(resultado.error or None) and resultado.error[:_MAX_ERROR],
                )
            )
            sesion.commit()
    except Exception as exc:  # noqa: BLE001 - deliberado: ver docstring del módulo (O13)
        logger.warning("no se pudo registrar la ejecución del cron: %s", exc)
