#!/usr/bin/env python3
"""Vigilancia diaria (specs/observabilidad, O19). La ejecuta GitHub Actions por la mañana.

Imprime un informe y sale con código 1 si alguna comprobación falla: el workflow queda en
rojo y GitHub manda el email. Solo LEE de la base de datos; no necesita la clave de cifrado.

Uso:
    uv run python scripts/vigilancia.py
    # Forzar el rojo para probar que el email llega (T11):
    uv run python scripts/vigilancia.py --caida-cobertura -1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fantasy.observabilidad.vigilancia import Umbrales, evaluar
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.fechas import ahora_en_madrid


def main(argv: list[str] | None = None) -> int:
    por_defecto = Umbrales()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--horas-sin-ejecucion", type=int, default=por_defecto.horas_sin_ejecucion)
    parser.add_argument("--horas-sin-snapshot", type=int, default=por_defecto.horas_sin_snapshot)
    parser.add_argument(
        "--caida-cobertura", type=float, default=por_defecto.caida_cobertura_puntos,
        help="puntos de caída que disparan el aviso (un valor negativo lo fuerza, para probar)",
    )
    args = parser.parse_args(argv)

    umbrales = Umbrales(
        horas_sin_ejecucion=args.horas_sin_ejecucion,
        horas_sin_snapshot=args.horas_sin_snapshot,
        caida_cobertura_puntos=args.caida_cobertura,
    )
    ahora = ahora_en_madrid()

    with obtener_fabrica_sesiones()() as sesion:
        comprobaciones = evaluar(sesion, ahora=ahora, umbrales=umbrales)

    print(f"Vigilancia de fantasy-scout — {ahora:%Y-%m-%d %H:%M %Z}\n")
    for c in comprobaciones:
        print(f"  [{'OK' if c.ok else 'ALERTA'}] {c.nombre}: {c.detalle}")

    fallidas = [c.nombre for c in comprobaciones if not c.ok]
    if fallidas:
        print(f"\n{len(fallidas)} alerta(s): {', '.join(fallidas)}. Detalle en /uso.")
        return 1
    print("\nTodo en orden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
