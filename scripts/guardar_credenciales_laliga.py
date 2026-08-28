#!/usr/bin/env python3
"""Guarda (cifradas) las credenciales de LaLiga Fantasy de un usuario, validándolas antes.

Con esto la app obtiene el token sola y deja de hacer falta pegarlo cada 24 h — que es lo
que hace viable el cron del paso 9. Ver design.md §Login automático para los riesgos.

Uso:
    uv run python scripts/guardar_credenciales_laliga.py --usuario tu@email.com
    uv run python scripts/guardar_credenciales_laliga.py --usuario tu@email.com --borrar
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from fantasy.auth import laliga_login
from fantasy.auth.credenciales_store import (
    borrar_credenciales,
    guardar_credenciales,
    obtener_token_valido,
)
from fantasy.storage.engine import obtener_fabrica_sesiones
from fantasy.storage.modelos import Usuario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usuario", required=True, help="Email con el que entras en fantasy-scout")
    parser.add_argument("--email-laliga", default=None, help="Si difiere del de fantasy-scout")
    parser.add_argument("--borrar", action="store_true", help="Elimina las credenciales guardadas")
    args = parser.parse_args()

    with obtener_fabrica_sesiones()() as sesion:
        usuario = sesion.scalar(select(Usuario).where(Usuario.email == args.usuario))
        if usuario is None:
            sys.exit(f"No existe el usuario {args.usuario!r}. Créalo con scripts/crear_usuario.py")

        if args.borrar:
            print("Credenciales borradas." if borrar_credenciales(sesion, usuario.id)
                  else "Ese usuario no tenía credenciales guardadas.")
            return

        email_laliga = args.email_laliga or args.usuario
        print(f"Credenciales de LaLiga Fantasy para {email_laliga}")
        print("(se guardarán CIFRADAS; la contraseña no se muestra al escribirla)")
        password = getpass.getpass("Contraseña de LaLiga: ")
        if not password:
            sys.exit("La contraseña no puede estar vacía.")

        # Se validan ANTES de guardar: no tiene sentido persistir credenciales que fallan.
        print("\nValidando contra LaLiga...")
        try:
            laliga_login.iniciar_sesion(email_laliga, password)
        except laliga_login.CredencialesInvalidas:
            sys.exit("LaLiga ha rechazado ese email o esa contraseña. No se ha guardado nada.")
        except laliga_login.ErrorLoginLaLiga as exc:
            sys.exit(f"No se ha podido contactar con el login de LaLiga: {exc}")

        guardar_credenciales(sesion, usuario.id, email_laliga, password)
        print("Credenciales validadas y guardadas cifradas.")

        token = obtener_token_valido(sesion, usuario.id)
        print("Token obtenido automáticamente." if token else "AVISO: no se pudo obtener el token.")


if __name__ == "__main__":
    main()
