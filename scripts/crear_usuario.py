#!/usr/bin/env python3
"""Alta manual de un usuario. No hay registro público (ver design.md §Modelo de auth).

Uso:
    uv run python scripts/crear_usuario.py --email x@y.z --password ...
    uv run python scripts/crear_usuario.py --email x@y.z   # pide la password por stdin
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from fantasy.auth.passwords import hashear_password
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import Usuario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", default=None, help="Si se omite, se pide por stdin")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if not password:
        print("La password no puede estar vacía.", file=sys.stderr)
        raise SystemExit(1)

    fabrica = obtener_fabrica_sesiones()
    with fabrica() as sesion:
        existente = sesion.scalar(select(Usuario).where(Usuario.email == args.email))
        if existente is not None:
            print(f"Ya existe un usuario con el email {args.email!r}.", file=sys.stderr)
            raise SystemExit(1)

        usuario = Usuario(email=args.email, password_hash=hashear_password(password))
        sesion.add(usuario)
        sesion.commit()
        print(f"Usuario creado: {usuario.email} ({usuario.id})")


if __name__ == "__main__":
    main()
