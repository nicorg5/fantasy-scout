"""Cliente HTTP para scraping: serial (nunca concurrente), espaciado en segundos,
User-Agent identificable, robots.txt comprobado por ruta. Reglas no negociables, ver
design.md §Reglas de scraping.
"""

from __future__ import annotations

import logging
import time

import httpx

from fantasy.config import obtener_config
from fantasy.scrapers import cache
from fantasy.scrapers.robots import verificar_permiso

# Ventana de validez de la caché de HTML scrapeado. El mercado se toma una vez al día
# (ver design.md §Cron), así que una ventana amplia evita re-scrapear entre ejecuciones
# del mismo día sin necesitar coordinación explícita.
VENTANA_CACHE_SEGUNDOS = 6 * 60 * 60

logger = logging.getLogger("fantasy.scrapers.cliente_http")


class ClienteScraping:
    """Un cliente por proceso: `_ultima_peticion` es de instancia, no global, para no
    serializar accidentalmente peticiones a orígenes distintos entre sí. Dentro de un
    mismo origen, sí queremos que se serialicen — por eso se comparte la instancia entre
    llamadas al mismo sitio, no se crea una por petición."""

    def __init__(self) -> None:
        self._config = obtener_config()
        self._ultima_peticion: float | None = None

    def _esperar_intervalo(self) -> None:
        if self._ultima_peticion is None:
            return
        transcurrido = time.monotonic() - self._ultima_peticion
        restante = self._config.scrape_intervalo_segundos - transcurrido
        if restante > 0:
            logger.info("esperando %.1fs antes de la siguiente petición (rate limit)", restante)
            time.sleep(restante)

    def get_html(self, url: str) -> str:
        """Comprueba robots.txt, usa caché si es válida, o hace UNA petición GET.

        Si hay HIT de caché, no se llega a comprobar robots.txt ni a esperar el
        intervalo: no hay petición de red que autorizar (ver R21).
        """
        html_cacheado = cache.leer(url, VENTANA_CACHE_SEGUNDOS)
        if html_cacheado is not None:
            return html_cacheado

        user_agent = self._config.user_agent
        verificar_permiso(url, user_agent)

        self._esperar_intervalo()
        logger.info("GET %s", url)
        respuesta = httpx.get(url, headers={"User-Agent": user_agent}, timeout=20.0)
        self._ultima_peticion = time.monotonic()
        respuesta.raise_for_status()

        cache.guardar(url, respuesta.text)
        return respuesta.text

    def get_html_seguro(self, url: str) -> str | None:
        """Igual que `get_html`, pero nunca lanza: un fallo de red o HTTP se traduce en
        `None`. Es lo que usa cualquier llamador que no pueda permitirse tumbar una
        petición web por culpa de un sitio de terceros caído (regla dura de degradación,
        ver design.md §Fuente 2). `RutaNoPermitida` de robots.txt sí se deja pasar: eso
        es una decisión nuestra, no un fallo del sitio, y merece verse.
        """
        try:
            return self.get_html(url)
        except httpx.HTTPError as exc:
            logger.warning("scraping degradado: fallo de red/HTTP en %s: %s", url, exc)
            return None
