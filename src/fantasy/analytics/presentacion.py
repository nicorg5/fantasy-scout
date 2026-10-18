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
class ProximoPartido:
    """Próximo partido de LaLiga del equipo del jugador. Dato scrapeado, con su origen."""

    rival: str
    en_casa: bool
    jornada: int
    origen: str
    capturado_en: datetime

    @property
    def lado(self) -> str:
        return "Casa" if self.en_casa else "Fuera"


@dataclass(frozen=True)
class ProximoRival:
    """La celda "Próximo rival": o un partido, o el motivo de que no lo haya.

    Va aparte de `BloqueAnalitico` a propósito (specs/proximo-rival, D4): aquel depende
    de emparejar al JUGADOR con futbolfantasy, y el rival solo depende de su EQUIPO. Un
    jugador sin emparejar no tiene tendencia, pero su rival se sabe igual, y un fallo de
    uno no puede llevarse por delante al otro.

    Misma invariante que el resto: un dato ausente es `None` + motivo, nunca un hueco.
    """

    partido: ProximoPartido | None = None
    motivo: str | None = None

    def __post_init__(self) -> None:
        if (self.partido is None) == (self.motivo is None):
            raise ValueError("el próximo rival lleva un partido o un motivo, exactamente uno")
        if self.motivo is not None and not self.motivo.strip():
            raise ValueError("un próximo rival sin dato necesita un motivo legible")

    @classmethod
    def sin_dato(cls, motivo: str) -> ProximoRival:
        return cls(motivo=motivo)


@dataclass(frozen=True)
class JugadorPresentado:
    """Un jugador tal y como lo pinta una fila de /plantilla o /mercado."""

    id_oficial: str
    nombre: str
    equipo: str
    posicion: str
    valor_mercado_euros: int
    analitica: BloqueAnalitico
    # Obligatorio a propósito: con un valor por defecto, una pantalla que olvidara
    # rellenarlo enseñaría "sin dato" sin que nada fallara.
    proximo_rival: ProximoRival
    # Obligatorio por lo mismo. None NO es "sin dato": es "esta pantalla no tiene la
    # columna" (/mercado, donde un jugador libre no tiene cláusula). En /plantilla siempre
    # lleva uno de los tres estados (specs/clausula-plantilla, D4).
    proteccion_clausula: ProteccionClausula | None
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


def formatear_espera(segundos: float) -> str:
    """'2d 5h', '3h 20m' o '15m'. Un número de segundos no dice nada de un vistazo.

    Una sola función para Clausulazos y Mi plantilla (specs/clausula-plantilla, C3): el
    mismo tiempo no puede leerse distinto en dos pantallas.
    """
    total = int(segundos)
    dias, resto = divmod(total, 86400)
    horas, resto = divmod(resto, 3600)
    minutos = resto // 60
    if dias:
        return f"{dias}d {horas}h"
    if horas:
        return f"{horas}h {minutos}m"
    return f"{minutos}m"


EstadoClausula = Literal["protegido", "clausulable", "blindado"]
AvisoClausula = Literal["clausulable", "pronto"]

UMBRAL_AVISO_SEGUNDOS = 24 * 3600
"""Por debajo de esto, un jugador protegido se resalta como "menos de 24 h"."""


@dataclass(frozen=True)
class ProteccionClausula:
    """Cuánto le queda a un jugador propio hasta que otro manager pueda clausularlo.

    Dato oficial (`buyoutClauseLockedEndTime` e `isShielded`). Las reglas viven aquí y no
    en la plantilla (specs/clausula-plantilla, D2): un estado incoherente, como
    "protegido" sin tiempo, no se puede construir.
    """

    estado: EstadoClausula
    # Solo en "protegido", y siempre > 0: con 0 s ya es clausulable.
    segundos_restantes: float | None = None

    def __post_init__(self) -> None:
        if self.estado == "protegido":
            if self.segundos_restantes is None or self.segundos_restantes <= 0:
                raise ValueError("un jugador protegido necesita un tiempo restante positivo")
        elif self.estado in ("clausulable", "blindado"):
            if self.segundos_restantes is not None:
                raise ValueError(f"un jugador {self.estado} no lleva tiempo restante")
        else:
            raise ValueError(f"estado de cláusula desconocido: {self.estado!r}")

    @classmethod
    def desde_bloqueo(
        cls, bloqueada_hasta: datetime | None, blindado: bool, ahora: datetime
    ) -> ProteccionClausula:
        """El blindaje manda sobre la fecha; sin fecha, o ya vencida, es clausulable."""
        if blindado:
            return cls(estado="blindado")
        if bloqueada_hasta is None or bloqueada_hasta <= ahora:
            return cls(estado="clausulable")
        return cls(estado="protegido", segundos_restantes=(bloqueada_hasta - ahora).total_seconds())

    @property
    def legible(self) -> str:
        if self.estado == "protegido":
            return f"protegido · {formatear_espera(self.segundos_restantes)}"
        return self.estado

    @property
    def aviso(self) -> AvisoClausula | None:
        """Qué resaltar. El blindado no: nadie puede llevárselo."""
        if self.estado == "clausulable":
            return "clausulable"
        if self.estado == "protegido" and self.segundos_restantes < UMBRAL_AVISO_SEGUNDOS:
            return "pronto"
        return None


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
    proximo_rival: ProximoRival

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
        return formatear_espera(self.segundos_para_desbloqueo)

    @property
    def origen_valor(self) -> str:
        """Valor, clausula y manager vienen de la API oficial."""
        return "oficial"
