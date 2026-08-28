"""Bloque C — cliente de la API oficial (paso 3).

Las fixtures salen de respuestas reales anonimizadas (`scripts/generar_fixtures.py`), así
que estos tests comprueban la forma que la API devolvió de verdad, no una inventada.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fantasy.official.errores import RespuestaInesperada
from fantasy.official.modelos import (
    DISCR_VENTA_DE_MANAGER,
    JugadorOficial,
    Plantilla,
    parsear_mercado,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mercado_crudo() -> list:
    return json.loads((FIXTURES / "market.json").read_text())


@pytest.fixture
def plantilla_cruda() -> dict:
    return json.loads((FIXTURES / "plantilla.json").read_text())


def test_mercado_descarta_las_ventas_de_otros_managers(mercado_crudo):
    """Solo `marketPlayerLeague` entra en el MVP (decisión del usuario)."""
    assert any(e["discr"] == DISCR_VENTA_DE_MANAGER for e in mercado_crudo), (
        "la fixture debe incluir un elemento de manager para que el test valga"
    )

    subastas = parsear_mercado(mercado_crudo)

    assert len(subastas) == sum(
        1 for e in mercado_crudo if e["discr"] != DISCR_VENTA_DE_MANAGER
    )


def test_mercado_parsea_los_campos_que_usa_la_ui(mercado_crudo):
    subasta = parsear_mercado(mercado_crudo)[0]

    assert subasta.jugador.id and isinstance(subasta.jugador.id, str)
    assert subasta.jugador.slug
    assert subasta.precio_venta > 0
    assert subasta.jugador.valor_mercado > 0
    assert subasta.expira_en.tzinfo is not None, "la fecha debe llevar zona horaria"


def test_plantilla_se_parsea(plantilla_cruda):
    plantilla = Plantilla.desde_api(plantilla_cruda)

    assert plantilla.jugadores
    assert all(j.jugador.nombre for j in plantilla.jugadores)
    assert plantilla.valor_equipo > 0


def test_campo_ausente_nombra_el_campo(mercado_crudo):
    """R16: no un KeyError pelado ni un None silencioso."""
    mutilado = json.loads(json.dumps(mercado_crudo))
    del mutilado[0]["playerMaster"]["marketValue"]

    with pytest.raises(RespuestaInesperada) as exc:
        parsear_mercado(mutilado)

    assert "marketValue" in str(exc.value)
    assert "marketValue" in exc.value.camino


def _jugador_minimo(**cambios) -> dict:
    base = {
        "id": "1", "name": "X", "nickname": "X", "slug": "x", "teamId": 1,
        "positionId": 2, "marketValue": 100,
        "playerStatus": "ok", "points": 0, "averagePoints": 0,
    }
    base.update(cambios)
    return base


def test_tipo_inesperado_tambien_falla_explicitamente():
    with pytest.raises(RespuestaInesperada) as exc:
        JugadorOficial.desde_api(_jugador_minimo(marketValue="no-soy-un-numero"))

    assert "marketValue" in exc.value.camino
    assert "str" in str(exc.value)


def test_las_dos_formas_de_playerMaster_dan_el_mismo_resultado():
    """Mercado trae `teamId` int; plantilla trae `team.id` como cadena. Verificado real."""
    forma_mercado = _jugador_minimo(teamId=12)
    forma_plantilla = _jugador_minimo()
    del forma_plantilla["teamId"]
    forma_plantilla["team"] = {"id": "12", "name": "Málaga CF", "slug": "malaga-cf"}

    assert (
        JugadorOficial.desde_api(forma_mercado).equipo_id
        == JugadorOficial.desde_api(forma_plantilla).equipo_id
        == 12
    )


def test_posicion_se_deriva_de_positionId_que_esta_en_ambos_endpoints():
    # La plantilla no trae `position` en texto, así que no puede ser la fuente.
    assert JugadorOficial.desde_api(_jugador_minimo(positionId=1)).posicion == "Portero"
    assert JugadorOficial.desde_api(_jugador_minimo(positionId=4)).posicion == "Delantero"


def test_posicion_nueva_no_pasa_desapercibida():
    """Si LaLiga añade una posición, hay que enterarse, no rellenar con un hueco."""
    with pytest.raises(RespuestaInesperada) as exc:
        JugadorOficial.desde_api(_jugador_minimo(positionId=99))

    assert "positionId" in exc.value.camino


def test_mercado_que_no_es_lista_falla_explicitamente():
    with pytest.raises(RespuestaInesperada):
        parsear_mercado({"data": []})


def test_sin_token_guardado_es_TokenInvalido(sesion_db, usuario_de_prueba):
    """T3.5/R13: la ausencia de token se trata igual que un 401, porque la acción que
    debe tomar el usuario es la misma: volver a pegarlo."""
    from fantasy.official.cliente import ClienteOficial
    from fantasy.official.errores import TokenInvalido

    usuario, _ = usuario_de_prueba
    cliente = ClienteOficial(sesion_db, usuario.id)

    with pytest.raises(TokenInvalido):
        cliente.obtener_mercado("012345678")


def test_token_caducado_es_TokenInvalido(sesion_db, usuario_de_prueba, jwt_falso):
    from datetime import datetime, timedelta, timezone

    from fantasy.auth.token_store import guardar_token
    from fantasy.official.cliente import ClienteOficial
    from fantasy.official.errores import TokenInvalido

    usuario, _ = usuario_de_prueba
    caducado = datetime.now(timezone.utc) - timedelta(hours=1)
    guardar_token(sesion_db, usuario.id, jwt_falso(caducado))

    cliente = ClienteOficial(sesion_db, usuario.id)
    with pytest.raises(TokenInvalido):
        cliente.obtener_mercado("012345678")
