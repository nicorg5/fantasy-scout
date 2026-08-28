"""Configuración leída del entorno, con defaults pensados para desarrollo local.

Los tres secretos (`DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET`) se leen
aquí y solo aquí; sus valores viven en el dashboard de Render, nunca en el repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

# Solo rellena variables no definidas ya en el entorno: en Render las variables viven
# en el dashboard y no hay .env, así que esto es un no-op en producción (ver R46).
load_dotenv()

NOMBRE_PROYECTO = "fantasy-scout"


@dataclass(frozen=True)
class Config:
    entorno: str
    database_url: str
    token_encryption_key: str
    session_secret: str
    retencion_dias: int
    scrape_intervalo_segundos: float
    contacto: str

    @property
    def es_local(self) -> bool:
        return self.entorno == "local"

    @property
    def user_agent(self) -> str:
        """User-Agent identificable: nombre del proyecto + contacto (ver R20)."""
        return f"{NOMBRE_PROYECTO}/0.1 (+{self.contacto})"


@lru_cache(maxsize=1)
def obtener_config() -> Config:
    return Config(
        entorno=os.getenv("FANTASY_ENTORNO", "local"),
        database_url=os.getenv("DATABASE_URL", ""),
        token_encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY", ""),
        session_secret=os.getenv("SESSION_SECRET", "secreto-solo-para-local"),
        retencion_dias=int(os.getenv("FANTASY_RETENCION_DIAS", "90")),
        scrape_intervalo_segundos=float(
            os.getenv("FANTASY_SCRAPE_INTERVALO_SEGUNDOS", "5")
        ),
        # Placeholder deliberado: define FANTASY_CONTACTO antes de scrapear (paso 4).
        contacto=os.getenv("FANTASY_CONTACTO", "mailto:sin-configurar@example.invalid"),
    )
