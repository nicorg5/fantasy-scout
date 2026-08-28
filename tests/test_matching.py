"""Bloque E — cruce de IDs (paso 5). El punto más frágil del proyecto.

Los casos de este fichero salen de datos reales observados el 2026-08-27, no inventados:
ver design.md §Cruce de IDs.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from fantasy.matching.emparejador import (
    MARGEN_AMBIGUEDAD,
    UMBRAL_POR_DEFECTO,
    CandidatoExterno,
    emparejar,
)
from fantasy.matching.normalizacion import normalizar, variantes_oficiales


@dataclass(frozen=True)
class OficialFalso:
    """Mismo contrato que `fantasy.official.modelos.JugadorOficial`, sin la API de por medio."""

    id: str
    nombre: str
    apodo: str
    slug: str
    equipo_id: int
    posicion: str = "Delantero"


# --- normalización ---

def test_normalizacion_quita_acentos_y_puntuacion():
    assert normalizar("Álvaro Núñez") == "alvaro nunez"
    assert normalizar("Á. Ortiz") == "a ortiz"


def test_variantes_incluyen_las_tres_formas():
    """Ninguna gana siempre: 'Larrubia' solo casa por name, 'Llorenç' solo por slug."""
    variantes = variantes_oficiales("David Larrubia", "Larrubia", "larrubia")
    assert "david larrubia" in variantes
    assert "larrubia" in variantes


# --- emparejamiento ---

def test_empareja_dentro_del_equipo(mapa_equipos_falso):
    oficial = OficialFalso("1", "Abel Bretones", "Abel Bretones", "abel-bretones", 12)
    candidatos = [CandidatoExterno("11230", "abel bretones", "11")]

    emparejados, sin = emparejar([oficial], candidatos)

    assert len(emparejados) == 1
    assert emparejados[0].candidato.id_externo == "11230"
    assert emparejados[0].confianza == 1.0
    assert sin == []


def test_caso_real_transliteracion(mapa_equipos_falso):
    """'Ihor Galdin' (oficial) vs 'igor galdin' (scrapeado): 0,91 — caso real."""
    oficial = OficialFalso("2", "Ihor Galdin", "Galdin", "ihor-galdin", 12)
    candidatos = [CandidatoExterno("18027", "igor galdin", "11")]

    emparejados, sin = emparejar([oficial], candidatos)

    assert len(emparejados) == 1
    assert 0.85 < emparejados[0].confianza < 1.0


def test_caso_peligroso_real_alfon(mapa_equipos_falso):
    """Caso real: 'Alfon' tenía dos candidatos, 'alfonso gonzalez' (su equipo) y
    'alfonso herrero' (otro equipo, y además en la plantilla del usuario).
    Acotar por equipo es lo que evita el emparejamiento catastrófico."""
    alfon = OficialFalso("3", "Alfon González", "Alfon", "alfonso-1", 17)
    candidatos = [
        CandidatoExterno("8703", "alfonso gonzalez", "17"),   # su equipo
        CandidatoExterno("9999", "alfonso herrero", "11"),    # OTRO equipo
    ]

    emparejados, _ = emparejar([alfon], candidatos)

    assert len(emparejados) == 1
    assert emparejados[0].candidato.nombre == "alfonso gonzalez"


def test_dos_candidatos_casi_iguales_no_se_adivinan(mapa_equipos_falso):
    """Salvaguarda de ambigüedad: ante dos casi idénticos del mismo equipo, sin match."""
    oficial = OficialFalso("4", "Juan Perez", "Perez", "juan-perez", 12)
    candidatos = [
        CandidatoExterno("1", "juan peres", "11"),
        CandidatoExterno("2", "juan peret", "11"),
    ]

    emparejados, sin = emparejar([oficial], candidatos)

    assert emparejados == []
    assert len(sin) == 1
    assert "ambiguo" in sin[0].motivo


def test_bajo_umbral_es_sin_match_no_el_mejor_candidato(mapa_equipos_falso):
    """R26: nunca se rellena con el mejor candidato a ciegas."""
    oficial = OficialFalso("5", "Zzzz Yyyy", "Zzzz", "zzzz-yyyy", 12)
    candidatos = [CandidatoExterno("1", "cristiano ronaldo", "11")]

    emparejados, sin = emparejar([oficial], candidatos)

    assert emparejados == []
    assert len(sin) == 1


def test_entrenadores_quedan_fuera(mapa_equipos_falso):
    """Decisión del usuario: fuera del MVP. Ni emparejados ni contados como fallo."""
    entrenador = OficialFalso("6", "Enrique Sánchez Flores", "Sánchez Flores",
                              "quique-sanchez-flores", 12, posicion="Entrenador")
    candidatos = [CandidatoExterno("e119", "quique sanchez", "11")]

    emparejados, sin = emparejar([entrenador], candidatos)

    assert emparejados == []
    assert sin == [], "un entrenador excluido no es un fallo de matching"


def test_equipo_sin_mapear_no_intenta_adivinar(mapa_equipos_falso):
    oficial = OficialFalso("7", "Nuevo Fichaje", "Nuevo", "nuevo-fichaje", 999)
    candidatos = [CandidatoExterno("1", "nuevo fichaje", "11")]

    emparejados, sin = emparejar([oficial], candidatos)

    assert emparejados == []
    assert "sin mapear" in sin[0].motivo


# --- overrides ---

def test_override_tiene_precedencia_sobre_la_heuristica(mapa_equipos_falso):
    """R25: un override que contradice a la heurística gana."""
    oficial = OficialFalso("8", "Abel Bretones", "Abel Bretones", "abel-bretones", 12)
    candidatos = [
        CandidatoExterno("11230", "abel bretones", "11"),  # lo que elegiría la heurística
        CandidatoExterno("55555", "otro distinto", "11"),  # lo que dice el override
    ]

    emparejados, _ = emparejar([oficial], candidatos, overrides={"8": "55555"})

    assert emparejados[0].candidato.id_externo == "55555"
    assert emparejados[0].confianza == 1.0


def test_override_que_apunta_a_un_id_inexistente_avisa(mapa_equipos_falso):
    oficial = OficialFalso("9", "Abel Bretones", "Abel Bretones", "abel-bretones", 12)
    candidatos = [CandidatoExterno("11230", "abel bretones", "11")]

    emparejados, sin = emparejar([oficial], candidatos, overrides={"9": "no-existe"})

    assert emparejados == []
    assert "override" in sin[0].motivo
