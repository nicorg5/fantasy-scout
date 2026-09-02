"""Tipos de dominio de presentación: el contrato que consumen los templates.

Regla no negociable del diseño: el **valor oficial** y el **bloque analítico scrapeado**
son categorías distintas y viven en secciones separadas. El bloque analítico puede venir
con estado ``unavailable`` sin que eso afecte al dato oficial, y ese estado se renderiza
como badge, nunca como un 0 ni un guion mudo.

Estos tipos los produce :mod:`fantasy.analytics.servicio` a partir de datos reales. En el
paso 1 los produjo un módulo de mock, ya retirado: el contrato no cambió al sustituirlo,
que era justo el objetivo de fijarlo antes de tener los datos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ORIGEN_OFICIAL = "oficial"
"""Etiqueta del único origen posible del valor de mercado: la API oficial de LaLiga."""

EstadoAnalitica = Literal["disponible", "unavailable"]
DireccionTendencia = Literal["sube", "baja", "estable"]


@dataclass(frozen=True)
class MediaSemanal:
    """Media diaria de variación de valor en los últimos 7 días.

    Es un dato **real y observado**, no una proyección: sale de `data-diferencia7` de
    futbolfantasy (variación acumulada de la semana), dividido entre 7.
    """

    media_diaria_euros: int
    acumulado_euros: int

    @property
    def direccion(self) -> str:
        if self.media_diaria_euros > 0:
            return "sube"
        if self.media_diaria_euros < 0:
            return "baja"
        return "estable"

    @property
    def simbolo(self) -> str:
        return {"sube": "▲", "baja": "▼", "estable": "="}[self.direccion]


@dataclass(frozen=True)
class TendenciaValor:
    """Tendencia del valor de mercado, tal y como la publica el sitio scrapeado."""

    direccion: DireccionTendencia
    variacion_euros: int

    @property
    def simbolo(self) -> str:
        return {"sube": "▲", "baja": "▼", "estable": "="}[self.direccion]


@dataclass(frozen=True)
class ProbabilidadJugar:
    """Probabilidad de ser titular en la próxima jornada, en porcentaje entero."""

    porcentaje: int

    def __post_init__(self) -> None:
        if not 0 <= self.porcentaje <= 100:
            raise ValueError(f"porcentaje fuera de rango: {self.porcentaje}")


@dataclass(frozen=True)
class BloqueAnalitico:
    """Sección analítica de un jugador. Separada del dato oficial a propósito.

    **Cada métrica tiene disponibilidad propia.** Decidido con datos reales
    (2026-08-27): futbolfantasy publica tendencia de valor para prácticamente todos los
    jugadores, pero probabilidad de jugar solo para los que entran en el once probable.
    Exigir ambas descartaba tendencia real de 4 de 11 jugadores.

    La invariante que sí se mantiene: **un dato ausente es siempre `None` + motivo**,
    nunca un 0 ni un valor por defecto que se confunda con información real.
    """

    estado: EstadoAnalitica
    tendencia_valor: TendenciaValor | None = None
    media_semanal: MediaSemanal | None = None
    probabilidad_jugar: ProbabilidadJugar | None = None
    origen: str | None = None
    capturado_en: datetime | None = None
    motivo: str | None = None
    motivo_probabilidad: str | None = None

    def __post_init__(self) -> None:
        if self.estado == "disponible":
            faltan = [
                nombre
                for nombre, valor in (
                    ("origen", self.origen),
                    ("capturado_en", self.capturado_en),
                )
                if valor is None
            ]
            if faltan:
                raise ValueError("analítica disponible sin " + ", ".join(faltan))
            if self.tendencia_valor is None and self.probabilidad_jugar is None:
                raise ValueError("analítica disponible sin ninguna métrica")
            # Una métrica ausente debe explicarse; si no, sería un hueco mudo.
            if self.probabilidad_jugar is None and not self.motivo_probabilidad:
                raise ValueError("falta la probabilidad y no se dice por qué")
        elif self.estado == "unavailable":
            if not self.motivo:
                raise ValueError("analítica no disponible sin motivo")
            if self.tendencia_valor is not None or self.probabilidad_jugar is not None:
                raise ValueError("analítica no disponible no puede traer datos")
        else:
            raise ValueError(f"estado de analítica desconocido: {self.estado!r}")

    @property
    def disponible(self) -> bool:
        """Hay al menos una métrica utilizable."""
        return self.estado == "disponible"

    @property
    def completa(self) -> bool:
        return self.disponible and self.probabilidad_jugar is not None

    @classmethod
    def desde_scraping(
        cls,
        *,
        tendencia_valor: TendenciaValor | None = None,
        media_semanal: MediaSemanal | None = None,
        probabilidad_jugar: ProbabilidadJugar | None = None,
        origen: str,
        capturado_en: datetime,
        motivo_probabilidad: str | None = None,
    ) -> BloqueAnalitico:
        return cls(
            estado="disponible",
            tendencia_valor=tendencia_valor,
            media_semanal=media_semanal,
            probabilidad_jugar=probabilidad_jugar,
            origen=origen,
            capturado_en=capturado_en,
            motivo_probabilidad=motivo_probabilidad,
        )

    @classmethod
    def no_disponible(cls, motivo: str, *, origen: str | None = None) -> BloqueAnalitico:
        """Degradación total: el fallo se representa con motivo, nunca con datos vacíos."""
        return cls(estado="unavailable", motivo=motivo, origen=origen)


@dataclass(frozen=True)
class JugadorPresentado:
    """Un jugador tal y como lo pinta una fila de /plantilla o /mercado."""

    id_oficial: str
    nombre: str
    equipo: str
    posicion: str
    valor_mercado_euros: int
    analitica: BloqueAnalitico
    origen_valor: str = ORIGEN_OFICIAL


SIGNO_TENDENCIA = {"sube": 1, "baja": -1, "estable": 0}


@dataclass(frozen=True)
class VariacionPlantilla:
    """Cuanto ha subido o bajado la plantilla en las ultimas 24h.

    Es la SUMA de la tendencia de cada jugador, asi que cuadra con la columna
    "Tendencia de valor" de la tabla de abajo: el total es literalmente lo que se ve
    sumado. Por eso sale de futbolfantasy y no de la API oficial, que no publica
    variacion de valor (solo el valor actual).
    """

    euros: int
    jugadores_con_dato: int
    jugadores_totales: int
    origen: str

    @property
    def direccion(self) -> str:
        if self.euros > 0:
            return "sube"
        if self.euros < 0:
            return "baja"
        return "estable"

    @property
    def simbolo(self) -> str:
        return {"sube": "▲", "baja": "▼", "estable": "="}[self.direccion]

    @property
    def completa(self) -> bool:
        """Si falta algun jugador, el total va acompanado de un aviso: una suma
        incompleta presentada como total seria enganosa."""
        return self.jugadores_con_dato == self.jugadores_totales


def calcular_variacion(jugadores: list[JugadorPresentado]) -> VariacionPlantilla | None:
    """Suma la variacion de 24h de una lista de jugadores.

    Devuelve None si NINGUNO tiene dato: mostrar "0 €" en ese caso seria mentir, porque
    no es que no haya variado, es que no lo sabemos.
    """
    con_dato = [
        j for j in jugadores
        if j.analitica.disponible and j.analitica.tendencia_valor is not None
    ]
    if not con_dato:
        return None

    total = sum(
        SIGNO_TENDENCIA[j.analitica.tendencia_valor.direccion]
        * j.analitica.tendencia_valor.variacion_euros
        for j in con_dato
    )
    return VariacionPlantilla(
        euros=total,
        jugadores_con_dato=len(con_dato),
        jugadores_totales=len(jugadores),
        # El origen es el de la analitica, no el oficial: es un dato scrapeado.
        origen=con_dato[0].analitica.origen or "futbolfantasy.com",
    )


def formatear_euros(cantidad: int) -> str:
    """1234567 -> '1.234.567 €' (separador de miles español)."""
    return f"{cantidad:,}".replace(",", ".") + " €"


@dataclass(frozen=True)
class ClausulaPresentada:
    """Una fila de la pantalla de clausulazos.

    Mismo criterio que `JugadorPresentado`: el bloque oficial y el analitico van
    separados y el analitico puede venir como no disponible sin afectar al resto.
    """

    id_oficial: str
    nombre: str
    manager: str
    equipo: str
    posicion: str
    valor_mercado_euros: int
    clausula_euros: int
    sobrepago_euros: int
    sobrepago_pct: float | None
    # None = ya se puede fichar. Si no, segundos que faltan para que se libere.
    segundos_para_desbloqueo: float | None
    blindado: bool
    analitica: BloqueAnalitico

    @property
    def fichable(self) -> bool:
        return not self.blindado and self.segundos_para_desbloqueo is None

    @property
    def espera_legible(self) -> str:
        """'2d 5h' o '3h 20m'. Un numero de segundos no dice nada de un vistazo."""
        if self.blindado:
            return "blindado"
        if self.segundos_para_desbloqueo is None:
            return "disponible"

        total = int(self.segundos_para_desbloqueo)
        dias, resto = divmod(total, 86400)
        horas, resto = divmod(resto, 3600)
        minutos = resto // 60
        if dias:
            return f"{dias}d {horas}h"
        if horas:
            return f"{horas}h {minutos}m"
        return f"{minutos}m"

    @property
    def origen_valor(self) -> str:
        """Valor, clausula y manager vienen de la API oficial."""
        return "oficial"
