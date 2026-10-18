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
    recolectar_analitica_de_bd,
)
from fantasy.matching.emparejador import CandidatoExterno, Emparejamiento
from fantasy.official.modelos import JugadorPlantilla, Plantilla, SubastaMercado
from fantasy.scrapers.degradacion import PaginaEquipo
from fantasy.scrapers.parsers import PartidoScrapeado, ProbabilidadScrapeada, TendenciaScrapeada

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
    jugador_plantilla = JugadorPlantilla(
        jugador=OFICIALES[0], clausula=1, blindado=False, bloqueada_hasta=None
    )
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


# --- Próximo rival (specs/proximo-rival, T5) ---

# Los dos oficiales son del Málaga: 12 en la API oficial, "11" en futbolfantasy.
PARTIDO_MALAGA = PartidoScrapeado(rival_nombre="Real Madrid", en_casa=False, jornada=3, capturado_en=AHORA)


def test_cada_jugador_sale_con_el_rival_de_su_equipo():
    """P4: el emparejado y el que no se pudo emparejar, los dos con rival (D1)."""
    analitica = _analitica_completa()
    analitica = Analitica(
        analitica.emparejamientos, analitica.tendencias, analitica.probabilidades,
        partidos={"11": PARTIDO_MALAGA},
    )

    for p in componer(OFICIALES, analitica):
        assert p.proximo_rival.partido.rival == "Real Madrid"
        assert p.proximo_rival.partido.lado == "Fuera"


def test_sin_tendencias_el_rival_se_sirve_igual():
    """P8: analítica no disponible, rival presente."""
    presentados = componer(OFICIALES, Analitica.vacia({"11": PARTIDO_MALAGA}))

    for p in presentados:
        assert not p.analitica.disponible
        assert p.proximo_rival.partido.rival == "Real Madrid"


def test_sin_partidos_el_rival_es_sin_dato_con_motivo():
    """P5: nunca un hueco mudo."""
    for p in componer(OFICIALES, _analitica_completa()):
        assert p.proximo_rival.partido is None
        assert p.proximo_rival.motivo


def test_de_bd_se_lee_el_rival_aunque_no_haya_analitica(sesion_db):
    """La ruta normal (P4): sin filas de analítica pero con partido guardado, el rival
    llega. Fecha imposible para no tocar datos reales."""
    from datetime import date

    from sqlalchemy import delete

    from fantasy.storage.analitica_repo import guardar_partidos_del_dia
    from fantasy.storage.modelos import AnaliticaDiaria, ProximoPartido

    dia = date(2099, 6, 1)
    try:
        sesion_db.execute(delete(AnaliticaDiaria).where(AnaliticaDiaria.fecha >= date(2099, 1, 1)))
        guardar_partidos_del_dia(sesion_db, dia, {"11": PARTIDO_MALAGA})
        with patch("fantasy.analytics.servicio.fecha_local", return_value=dia):
            analitica = recolectar_analitica_de_bd(sesion_db, OFICIALES)
    finally:
        sesion_db.execute(delete(ProximoPartido).where(ProximoPartido.fecha == dia))
        sesion_db.commit()

    assert not analitica.hay_datos
    assert analitica.partidos["11"].rival_nombre == "Real Madrid"


def test_en_vivo_se_recogen_los_partidos_en_la_misma_pasada():
    """P6: "Actualizar datos" refresca también el rival, con la misma descarga por equipo."""
    tendencia = TendenciaScrapeada(
        "11230", "abel bretones", "11", TendenciaValor(direccion="sube", variacion_euros=1), AHORA
    )
    nuevo = PartidoScrapeado(rival_nombre="Levante", en_casa=True, jornada=4, capturado_en=AHORA)
    descargas = []

    def pagina(_cliente, slug, nombre, **kwargs):
        descargas.append((slug, nombre, kwargs))
        return PaginaEquipo(partido=nuevo)

    with (
        patch("fantasy.analytics.servicio.obtener_tendencias_mercado", return_value=[tendencia]),
        patch("fantasy.analytics.servicio.obtener_pagina_equipo", side_effect=pagina),
    ):
        analitica = recolectar_analitica(OFICIALES, ignorar_cache=True)

    assert descargas == [("malaga", "Málaga", {"ignorar_cache": True})], "una por equipo"
    assert analitica.partidos == {"11": nuevo}
