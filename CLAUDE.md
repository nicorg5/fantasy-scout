# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

El código, los comentarios, los commits y las conversaciones de este repo están **en español**. Mantenlo así.

## Comandos

```bash
uv sync --all-groups                 # dependencias (gestor: uv, no pip)
docker compose up -d                 # Postgres local en el puerto 5433
uv run alembic upgrade head          # esquema
uv run uvicorn fantasy.api.app:app --reload   # http://localhost:8000

uv run pytest                        # suite completa
uv run pytest tests/test_rutas.py::test_nombre -q   # un solo test
uv run alembic revision --autogenerate -m "mensaje"

bash scripts/pre_deploy.sh           # comprobaciones antes de mergear a main
uv run python scripts/crear_usuario.py --email tu@email.com
uv run python scripts/snapshot_diario.py            # el job del cron
```

Los tests **necesitan el Postgres local levantado** (crean y borran datos) y las variables de
entorno de `.env`. Nunca los apuntes a la base de producción.

Ensayo contra producción sin desplegar (paso obligatorio antes de mergear):

```bash
uv run --env-file .env.produccion uvicorn fantasy.api.app:app --port 8010
uv run --env-file .env.produccion alembic upgrade head   # si hay migraciones nuevas
```

## Flujo de trabajo y despliegue

- Se commitea en **`feature/v0.1.0`**. `main` despliega solo a Render en cada push, así que
  solo recibe lo ya verificado (`git checkout main && git merge feature/v0.1.0 && git push`).
- **Las migraciones se aplican a la base de producción ANTES del merge.** Si el código llega
  al servidor antes que la tabla, la web se cae.
- Piezas: web en Render (`render.yaml`), Postgres en Neon, cron diario en GitHub Actions
  (`.github/workflows/snapshot-diario.yml`). **Cuatro disparos**: madrugada (23:30 y 01:00
  UTC) para refrescar la analítica tras el cambio de valor de las 00:00, y tarde (16:30 y
  17:30 UTC) para guardar el snapshot tras el cierre de mercado. El doble disparo de cada
  bloque cubre el cambio CET/CEST. El script decide mirando la **hora real de Madrid**,
  porque GitHub Actions retrasa los crons (se han visto 2 h de retraso).
- **Vigilancia** (`.github/workflows/vigilancia.yml`, 06:00 UTC): único canal de aviso. Si
  `scripts/vigilancia.py` sale ≠ 0, GitHub manda email. Solo lee la BD.
- Ningún secreto en el repo: en `render.yaml` van con `sync: false`.

## Arquitectura

FastAPI + Jinja2 + htmx sirviendo HTML desde el mismo proceso. **Sin build step de JS**: no
hay `package.json`; htmx, Pico.css y la fuente se cargan por CDN desde `templates/base.html`.

```
src/fantasy/
├── config.py     único punto de lectura del entorno (obtener_config(), cacheada)
├── auth/         login propio por cookie firmada + login contra LaLiga + cifrado Fernet
├── official/     cliente de la API no pública de LaLiga Fantasy
├── scrapers/     futbolfantasy.com (robots.txt por ruta, serial y espaciado, caché)
├── matching/     cruce de IDs entre ambas fuentes
├── storage/      modelos SQLAlchemy 2.0, fechas de Madrid, retención
├── analytics/    composición de datos y contrato de presentación
├── observabilidad/  registro de uso (middleware), panel /uso y vigilancia diaria
└── api/          rutas, plantillas y estilos
```

Cuatro invariantes que explican casi todo el código y **no deben romperse**:

1. **Las dos fuentes no se mezclan.** Oficial (API) y scrapeado (futbolfantasy) viajan en
   bloques separados y cada dato lleva su origen visible. Por eso `official/` **lanza
   excepciones** ante una respuesta rara y `scrapers/` **nunca lanza**: un fallo del scraping
   no puede tumbar la app, la analítica se marca como no disponible y se sigue sirviendo.
2. **Un dato ausente nunca se disfraza de dato real.** Nada de `0` ni guiones ambiguos: "sin
   dato" con su motivo. La invariante vive en el tipo (`analytics/presentacion.py`), no en la
   plantilla.
3. **El matching es el punto frágil.** Acota por equipo primero, compara nombres después y
   ante ambigüedad prefiere no emparejar. `origen` y `confianza` se guardan siempre para poder
   auditar. Overrides manuales versionados en `data/mappings/`.
4. **La web no scrapea nunca en la ruta normal.** El cron rellena `analitica_diaria` de noche
   y la web solo lee. El botón "Actualizar datos" (`/plantilla/tabla`, `/mercado/tabla`, con
   `en_vivo=True`) es la excepción explícita.

Errores esperados en las rutas: un token de LaLiga caducado (dura 24 h) es **evento normal**,
se responde 200 con aviso y enlace a `/token`, nunca un 500. Ver `_pagina_de_jugadores` y
`_fragmento_tabla` en [app.py](src/fantasy/api/app.py).

### htmx

Las rutas `*/tabla` devuelven **solo el fragmento** de tabla, no la página. Los errores dentro
de un fragmento se renderizan como `_tabla_error.html`, porque devolver la página entera la
metería dentro del hueco de la tabla. La navegación entre secciones va por `hx-boost` (no hay
recarga completa de página al cambiar de sección — tenlo en cuenta para cualquier cosa que
dependa de `DOMContentLoaded`).

### Datos

- `market_snapshot` es **caché operativa**, no fuente de verdad: perderla es recuperable.
- `analitica_diaria` se indexa por el id de **futbolfantasy**, no por el jugador oficial, para
  que una mejora del matching beneficie a los datos ya guardados sin migrarlos.
- `laliga_credentials` guarda la contraseña de LaLiga con cifrado **reversible** (hay que
  reenviarla para renovar el token). Decisión consciente y documentada: es lo que hace viable
  el cron desatendido.
- Fechas: siempre la **local de Madrid** para el corte diario (`storage/fechas.py`), no UTC.

## Observabilidad

- El middleware registra **cualquier ruta nueva sin tocar nada**. Si una ruta no es uso real
  de la app, añádela a `RUTAS_IGNORADAS` en `observabilidad/registro.py`.
- `registrar_evento()` es el **único** sitio que construye un `EventoUso`: si hay que mandar
  eventos a otro destino, se añade ahí.
- El registro de uso y el de ejecuciones del cron **nunca lanzan**: una pieza accesoria no
  tumba la principal (misma regla que `scrapers/`).
- Los tests que ejecutan `main()` de `snapshot_diario.py` neutralizan su registro con un
  `autouse`; los del propio registro llevan `@pytest.mark.registro_real`.
- **No lances `snapshot_diario.py` para verificar nada**: scrapea de verdad si la BD tiene
  credenciales guardadas. Usa los tests, que lo parchean.

## Reglas del scraping (no relajar)

`robots.txt` se comprueba antes de tocar **cada ruta nueva**; peticiones seriales y espaciadas
en segundos, nunca concurrentes; User-Agent identificable con `FANTASY_CONTACTO`; caché local.
`analiticafantasy.com` se descartó como segunda fuente porque su `robots.txt` la prohíbe.

## Especificaciones

`specs/mvp/` y `specs/observabilidad/` (`requirements`, `design`, `tasks`) están **fuera del
control de versiones** pero el código los referencia por identificador (`R13`, `R31`, `design.md §Cron`). Si vas a tocar algo
con una referencia así, léelos antes.
