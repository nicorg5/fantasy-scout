"""Exporta la maqueta a un HTML autocontenido, para validarla desde el móvil.

Renderiza `/plantilla` y `/mercado` con el TestClient (sin levantar servidor ni tocar la
red), incrusta Pico.css y los estilos propios, y rellena
`scripts/maqueta_previa.template.html`.

Uso:
    uv run python scripts/exportar_maqueta.py [destino.html]
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient

from fantasy.api.app import app

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLA_PREVIA = RAIZ / "scripts" / "maqueta_previa.template.html"
ESTILOS_PROPIOS = RAIZ / "src" / "fantasy" / "api" / "static" / "estilos.css"
PICO_CACHE = RAIZ / "data" / "cache" / "pico.min.css"
PICO_URL = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"


def obtener_pico() -> str:
    """Pico se sirve por CDN en la app; para el HTML autocontenido hay que traerlo."""
    if not PICO_CACHE.exists():
        PICO_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(PICO_URL, timeout=30) as respuesta:
            PICO_CACHE.write_bytes(respuesta.read())
    return PICO_CACHE.read_text(encoding="utf-8")


def incrustar_estilos(html: str, pico: str, propios: str) -> str:
    """Sustituye los dos <link> de la página por el CSS incrustado."""
    lineas = [
        linea
        for linea in html.splitlines()
        if 'rel="stylesheet"' not in linea
    ]
    documento = "\n".join(lineas)
    bloque = f"<style>\n{pico}\n</style>\n<style>\n{propios}\n</style>"
    return documento.replace("</head>", f"{bloque}\n</head>", 1)


def main() -> int:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "data" / "cache" / "maqueta.html"
    pico = obtener_pico()
    propios = ESTILOS_PROPIOS.read_text(encoding="utf-8")

    documentos: dict[str, str] = {}
    with TestClient(app) as cliente:
        for pantalla in ("plantilla", "mercado"):
            respuesta = cliente.get(f"/{pantalla}")
            respuesta.raise_for_status()
            documentos[pantalla] = incrustar_estilos(respuesta.text, pico, propios)

    for nombre, documento in documentos.items():
        if "</script" in documento.lower():
            raise SystemExit(
                f"la pantalla {nombre} contiene '</script', no se puede incrustar tal cual"
            )

    salida = PLANTILLA_PREVIA.read_text(encoding="utf-8")
    salida = salida.replace("__DOC_PLANTILLA__", documentos["plantilla"])
    salida = salida.replace("__DOC_MERCADO__", documentos["mercado"])

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(salida, encoding="utf-8")
    print(f"escrito {destino} ({destino.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
