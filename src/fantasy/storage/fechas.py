"""Fecha local de Madrid: la referencia temporal del snapshot diario.

**Nunca se usa la hora local del contenedor** (Render corre en UTC) ni `datetime.now()`
sin zona. El mercado cierra a las 18:00 de Madrid y España alterna CET/CEST, así que
convertir explícitamente es lo único que evita que el snapshot se atribuya al día
equivocado (ver design.md §Cron).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

# El mercado de jugadores libres cierra a esta hora local (verificado: los 13 jugadores
# libres del mercado real expiraban todos a las 18:00:00 exactas).
HORA_CIERRE_MERCADO = 18


def ahora_en_madrid() -> datetime:
    return datetime.now(tz=MADRID)


def fecha_local(momento: datetime | None = None) -> date:
    """Fecha del calendario en Madrid. Un `datetime` sin zona se asume UTC, no local."""
    momento = momento or ahora_en_madrid()
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(MADRID).date()


def mercado_ya_cerro(momento: datetime | None = None) -> bool:
    """True si en Madrid ya pasó la hora de cierre. Es la guardia del cron (R42): impide
    que un disparo temprano capture el mercado todavía abierto."""
    momento = momento or ahora_en_madrid()
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(MADRID).hour >= HORA_CIERRE_MERCADO
