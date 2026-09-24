"""Panel `/uso`: lo que pasa en la app, solo para el administrador (specs/observabilidad).

Vive dentro de la propia app (design.md §D8) y no aparece en el menú: el resto de usuarios
no tiene por qué saber que existe. Sus visitas no se registran (ver `RUTAS_IGNORADAS`).

Arriba muestra las **mismas comprobaciones que la vigilancia diaria**, con la misma función
(`vigilancia.evaluar`): el panel y el email de la mañana no pueden contradecirse.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fantasy.auth.dependencias import usuario_actual
from fantasy.auth.sesiones import NOMBRE_COOKIE, leer_usuario_id_de_cookie
from fantasy.config import obtener_config
from fantasy.observabilidad import consultas
from fantasy.observabilidad.registro import ACCION_ACTUALIZAR_DATOS, ACCION_FILTRAR_CLAUSULAS
from fantasy.observabilidad.vigilancia import evaluar
from fantasy.storage.engine import obtener_fabrica_sesiones, obtener_sesion
from fantasy.storage.fechas import MADRID, ahora_en_madrid, fecha_local
from fantasy.storage.modelos import TipoEvento, Usuario

logger = logging.getLogger("fantasy.observabilidad")

router = APIRouter()

_templates: Jinja2Templates | None = None

PERIODOS_DIAS = (7, 30)

# Nombres legibles: la tabla guarda plantillas de ruta, que no son lo que ve una persona.
NOMBRES_RUTA = {
    "/plantilla": "Plantilla",
    "/mercado": "Mercado",
    "/clausulas": "Clausulazos",
    "/token": "Token",
    "/login": "Login",
    # Los fragmentos del botón "Actualizar datos" pertenecen a su pantalla.
    "/plantilla/tabla": "Plantilla",
    "/mercado/tabla": "Mercado",
}
NOMBRES_ACCION = {
    ACCION_ACTUALIZAR_DATOS: "Actualizar datos",
    ACCION_FILTRAR_CLAUSULAS: "Filtrar clausulazos",
}


def _nombre_ruta(ruta: str | None) -> str:
    if ruta is None:
        return "—"
    return NOMBRES_RUTA.get(ruta, ruta)


def _duracion(valor: timedelta | None) -> str:
    """`None` es "sin dato", nunca "0 s" (invariante nº 2)."""
    if valor is None:
        return "sin dato"
    if valor < timedelta(seconds=1):
        # Truncar a "0 s" se leería como "sin dato": aquí sí hay dato, y es muy corto.
        return "menos de 1 s"
    segundos = int(valor.total_seconds())
    if segundos < 60:
        return f"{segundos} s"
    minutos, segundos = divmod(segundos, 60)
    if minutos < 60:
        return f"{minutos} min {segundos:02d} s"
    horas, minutos = divmod(minutos, 60)
    return f"{horas} h {minutos:02d} min"


def _hace(momento: datetime | None, ahora: datetime | None = None) -> str:
    if momento is None:
        return "nunca"
    segundos = ((ahora or ahora_en_madrid()) - momento).total_seconds()
    if segundos < 3600:
        return f"hace {max(1, int(segundos // 60))} min"
    if segundos < 86400:
        return f"hace {int(segundos // 3600)} h"
    dias = int(segundos // 86400)
    return f"hace {dias} día{'s' if dias != 1 else ''}"


def _madrid(momento: datetime | None) -> str:
    return momento.astimezone(MADRID).strftime("%d/%m %H:%M") if momento else "—"


def montar_templates(templates: Jinja2Templates) -> None:
    global _templates
    _templates = templates
    templates.env.filters.update(
        nombre_ruta=_nombre_ruta, duracion=_duracion, hace=_hace, madrid=_madrid
    )
    templates.env.globals["es_admin"] = es_admin


# id del admin por email. No se guarda un "no existe": el admin puede crearse después.
_ids_admin: dict[str, uuid.UUID] = {}


def es_admin(request: Request | None) -> bool:
    """Si quien navega es el administrador. **Solo decide si se pinta el enlace del menú**:
    la protección de verdad es la dependencia `administrador` de la ruta.

    Se llama en cada página, así que no puede costar ni romper nada: el id del admin se
    busca una vez por proceso, y cualquier fallo significa simplemente "no pintar".
    """
    admin = obtener_config().admin_email
    if not admin or request is None:
        return False
    usuario_id = leer_usuario_id_de_cookie(request.cookies.get(NOMBRE_COOKIE))
    if usuario_id is None:
        return False

    if admin not in _ids_admin:
        try:
            with obtener_fabrica_sesiones()() as sesion:
                encontrado = sesion.scalar(select(Usuario.id).where(func.lower(Usuario.email) == admin))
        except Exception as exc:  # noqa: BLE001 - un enlace de menú no tumba una página
            logger.warning("no se pudo comprobar si el usuario es admin: %s", exc)
            return False
        if encontrado is None:
            return False
        _ids_admin[admin] = encontrado

    return usuario_id == _ids_admin[admin]


def administrador(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    """Deja pasar solo a `FANTASY_ADMIN_EMAIL`. Sin sesión, `usuario_actual` ya redirige a
    /login.

    **404 y no 403**: un 403 confirmaría que aquí hay algo. Y si la variable está vacía
    (en local o en los tests), nadie pasa: fallo cerrado por defecto.
    """
    admin = obtener_config().admin_email
    if not admin or usuario.email.strip().lower() != admin:
        raise HTTPException(status_code=404)
    return usuario


@router.get("/uso")
def panel_de_uso(
    request: Request,
    dias: int = 7,
    admin: Usuario = Depends(administrador),
    sesion: Session = Depends(obtener_sesion),
):
    assert _templates is not None
    dias = dias if dias in PERIODOS_DIAS else PERIODOS_DIAS[0]
    ahora = ahora_en_madrid()
    # Días naturales de Madrid: "7 días" es hoy y los 6 anteriores enteros.
    desde = consultas.inicio_del_dia(ahora) - timedelta(days=dias - 1)

    uso = consultas.uso_por_ruta(sesion, desde=desde)
    navegaciones = [u for u in uso if u.tipo is TipoEvento.NAVEGACION]
    total_navegaciones = sum(u.veces for u in navegaciones)

    contexto = {
        "titulo": "Uso de la app",
        "dias": dias,
        "periodos": PERIODOS_DIAS,
        "ahora": ahora,
        "comprobaciones": evaluar(sesion, ahora=ahora),
        "activos": consultas.usuarios_activos(sesion, ahora=ahora),
        "resumen": consultas.resumen_sesiones(sesion, desde=desde),
        "navegaciones": navegaciones,
        "total_navegaciones": total_navegaciones,
        "acciones": [u for u in uso if u.tipo is TipoEvento.ACCION],
        "nombres_accion": NOMBRES_ACCION,
        "usuarios": consultas.detalle_por_usuario(sesion, desde=desde),
        "latencias": consultas.latencia_por_ruta(sesion, desde=desde),
        "tokens": consultas.estado_tokens(sesion),
        "ejecuciones": consultas.ultimas_ejecuciones(sesion),
        "cobertura": list(reversed(consultas.cobertura_diaria(sesion, hasta=fecha_local(ahora)))),
    }
    return _templates.TemplateResponse(request=request, name="uso.html", context=contexto)
