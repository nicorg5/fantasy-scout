"""T7.1/T7.4 — composición oficial + analítica, y la regla dura de degradación (R35)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from fantasy.analytics.presentacion import ProbabilidadJugar, TendenciaValor
from fantasy.analytics.servicio import (
    Analitica,
    componer,
    obtener_mercado,
    obtener_plantilla,
    recolectar_analitica,
)
from fantasy.matching.emparejador import CandidatoExterno, Emparejamiento
from fantasy.official.modelos import JugadorPlantilla, Plantilla, SubastaMercado
from fantasy.scrapers.parsers import ProbabilidadScrapeada, TendenciaScrapeada

AHORA = datetime.now(timezone.utc)


@dataclass(frozen=True)
class OficialFalso:
    id: str
    nombre: str
    apodo: str
    slug: str
    equipo_id: int
    valor_mercado: int = 5_000_000
    posicion: str = "Defensa"


OFICIALES = [
    OficialFalso("1", "Abel Bretones", "Abel Bretones", "abel-bretones", 12),
    OficialFalso("2", "David Larrubia", "Larrubia", "larrubia", 12),
]


def _analitica_completa() -> Analitica:
    candidato = CandidatoExterno("11230", "abel bretones", "11")
    return Analitica(
        emparejamientos={"1": Emparejamiento("1", candidato, 1.0)},
        tendencias={
            "11230": TendenciaScrapeada(
                "11230", "abel bretones", "11",
                TendenciaValor(direccion="sube", variacion_euros=42_000), AHORA,
            )
        },
        probabilidades={
            "11230": ProbabilidadScrapeada(id_futbolfantasy="11230", slug="abel-bretones", probabilidad=ProbabilidadJugar(porcentaje=75), capturado_en=AHORA)
        },
    )


def test_jugador_emparejado_sale_con_su_analitica():
    presentados = componer(OFICIALES, _analitica_completa())

    abel = next(p for p in presentados if p.id_oficial == "1")
    assert abel.analitica.disponible
    assert abel.analitica.probabilidad_jugar.porcentaje == 75
    assert abel.analitica.tendencia_valor.direccion == "sube"


def test_jugador_sin_emparejar_sale_como_no_disponible_pero_con_su_valor_oficial():
    """La analítica falta, el dato oficial NO."""
    presentados = componer(OFICIALES, _analitica_completa())

    larrubia = next(p for p in presentados if p.id_oficial == "2")
    assert not larrubia.analitica.disponible
    assert larrubia.analitica.motivo
    assert larrubia.valor_mercado_euros == 5_000_000, "el dato oficial se sirve igual"


def test_scraping_caido_del_todo_sigue_sirviendo_lo_oficial():
    """**R35, el requirement que no se negocia.**"""
    presentados = componer(OFICIALES, Analitica.vacia())

    assert len(presentados) == len(OFICIALES)
    for p in presentados:
        assert p.valor_mercado_euros > 0, "los datos oficiales se siguen sirviendo"
        assert not p.analitica.disponible
        assert p.analitica.motivo
        # Nunca un 0 ni un hueco que se confunda con dato real.
        assert p.analitica.tendencia_valor is None
        assert p.analitica.probabilidad_jugar is None


def test_recolectar_analitica_no_lanza_si_el_scraper_falla():
    """Si el sitio está caído, se devuelve analítica vacía en vez de una excepción."""
    with patch("fantasy.analytics.servicio.obtener_tendencias_mercado", return_value=[]):
        analitica = recolectar_analitica(OFICIALES)

    assert analitica.emparejamientos == {}
    assert not analitica.hay_datos


def _cliente_oficial_falso(*, mercado=None, plantilla=None):
    """Doble de `ClienteOficial`: evita tocar sesión de BD ni red en estos tests, que solo
    comprueban qué recolector de analítica se elige según `en_vivo`."""
    from unittest.mock import MagicMock

    cliente = MagicMock()
    cliente.obtener_liga_y_equipo.return_value = ("liga-1", 1)
    if mercado is not None:
        cliente.obtener_mercado.return_value = mercado
    if plantilla is not None:
        cliente.obtener_plantilla.return_value = plantilla
    return cliente


def test_obtener_mercado_en_vivo_scrapea_al_momento():
    """Decisión del usuario (2026-09-02): el botón 'Actualizar datos' debe volver a
    scrapear futbolfantasy.com, no solo releer la BD."""
    subasta = SubastaMercado(
        id="1", jugador=OFICIALES[0], precio_venta=1, expira_en=AHORA, numero_pujas=0
    )
    cliente = _cliente_oficial_falso(mercado=[subasta])

    with (
        patch("fantasy.analytics.servicio.ClienteOficial", return_value=cliente),
        patch("fantasy.analytics.servicio.recolectar_analitica", return_value=Analitica.vacia()) as en_vivo,
        patch("fantasy.analytics.servicio.recolectar_analitica_de_bd") as en_bd,
    ):
        obtener_mercado(sesion=None, usuario_id=None, en_vivo=True)

    en_vivo.assert_called_once()
    en_bd.assert_not_called()


def test_obtener_mercado_por_defecto_lee_de_bd():
    """La carga normal de la página sigue siendo instantánea: no scrapea en cada visita."""
    subasta = SubastaMercado(
        id="1", jugador=OFICIALES[0], precio_venta=1, expira_en=AHORA, numero_pujas=0
    )
    cliente = _cliente_oficial_falso(mercado=[subasta])

    with (
        patch("fantasy.analytics.servicio.ClienteOficial", return_value=cliente),
        patch("fantasy.analytics.servicio.recolectar_analitica") as en_vivo,
        patch("fantasy.analytics.servicio.recolectar_analitica_de_bd", return_value=Analitica.vacia()) as en_bd,
    ):
        obtener_mercado(sesion=None, usuario_id=None)

    en_bd.assert_called_once()
    en_vivo.assert_not_called()


def test_obtener_plantilla_en_vivo_scrapea_al_momento():
    jugador_plantilla = JugadorPlantilla(jugador=OFICIALES[0], clausula=1, blindado=False)
    cliente = _cliente_oficial_falso(
        plantilla=Plantilla(jugadores=[jugador_plantilla], dinero=0, valor_equipo=0, puntos=0)
    )

    with (
        patch("fantasy.analytics.servicio.ClienteOficial", return_value=cliente),
        patch("fantasy.analytics.servicio.recolectar_analitica", return_value=Analitica.vacia()) as en_vivo,
        patch("fantasy.analytics.servicio.recolectar_analitica_de_bd") as en_bd,
    ):
        obtener_plantilla(sesion=None, usuario_id=None, en_vivo=True)

    en_vivo.assert_called_once()
    en_bd.assert_not_called()
