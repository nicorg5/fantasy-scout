# fantasy-scout

Analítica de **LaLiga Fantasy** para una liga privada: tu plantilla y el mercado del día,
enriquecidos con tendencia de valor y probabilidad de jugar.

Proyecto personal, para un grupo cerrado de amigos. No es un producto ni tiene ánimo
comercial.

> **Aviso**: se apoya en la API de LaLiga Fantasy, que **no es pública ni está
> documentada**, y en scraping de un sitio de terceros. Ambas cosas pueden dejar de
> funcionar sin previo aviso. Si reutilizas este código, cuenta con ello.

---

## Qué hace

Dos pantallas, ambas tras login:

| Pantalla | Contenido |
|---|---|
| `/plantilla` | Tus jugadores, con valor oficial y analítica |
| `/mercado` | Las subastas de jugadores libres del día |

Por cada jugador: **valor de mercado**, **tendencia de las últimas 24 h**, **media diaria
de los últimos 7 días** y **probabilidad de jugar**.

Además, un job diario guarda una foto del mercado tras el cierre (18:00, hora de Madrid).

## Cómo está montado

```
src/fantasy/
├── config.py       Lectura de entorno (única fuente de los secretos)
├── auth/           Login propio, sesión, cifrado de credenciales y login contra LaLiga
├── official/       Cliente de la API oficial de LaLiga Fantasy
├── scrapers/       Scraping de futbolfantasy.com (robots.txt, rate limit, caché)
├── matching/       Cruce de IDs entre ambas fuentes
├── storage/        Modelos SQLAlchemy, fechas de Madrid, retención
├── analytics/       Contrato de presentación y composición de datos
└── api/            FastAPI + Jinja2 + htmx
```

**Stack**: Python 3.12, FastAPI, SQLAlchemy 2.0, Postgres, Jinja2 + htmx + Pico.css. Sin
build step de JS: no hay `package.json` ni bundler, y htmx se carga por CDN.

### Tres decisiones que explican casi todo el código

**1. Las dos fuentes de datos nunca se mezclan.** Los datos oficiales (API de LaLiga) y
los scrapeados (futbolfantasy) viajan en bloques separados y cada dato lleva su origen
visible en pantalla. Un fallo del scraping **no puede tumbar la app**: plantilla y mercado
se siguen sirviendo con los datos oficiales y la analítica marcada como *no disponible*.
Por eso `official/` lanza excepciones ante una respuesta rara y `scrapers/` nunca lo hace.

**2. Un dato ausente jamás se disfraza de dato real.** Si falta la probabilidad de jugar,
se muestra "sin dato" con su motivo — nunca un `0` ni un guion que pueda leerse como
información. La invariante está en el propio tipo (`analytics/presentacion.py`), no en la
plantilla, para que no dependa de acordarse.

**3. El cruce de IDs es el punto frágil, y se trata como tal.** Las dos fuentes usan
identificadores de jugador incompatibles. El emparejamiento **acota primero por equipo** y
compara nombres después; ante ambigüedad, prefiere no emparejar. Un jugador mal
emparejado es peor que uno sin analítica, porque contamina en silencio un dato que se
mira a diario. Hay overrides manuales versionados en `data/mappings/` para corregir a mano
lo que la heurística falle.

## Puesta en marcha

Requiere [uv](https://docs.astral.sh/uv/) y Docker.

```bash
# 1. Dependencias y base de datos local
uv sync --all-groups
docker compose up -d              # Postgres en el puerto 5433

# 2. Configuración
cp .env.example .env              # y rellena los valores (ver más abajo)
uv run alembic upgrade head

# 3. Tu cuenta
uv run python scripts/crear_usuario.py --email tu@email.com

# 4. Arrancar
uv run uvicorn fantasy.api.app:app --reload    # http://localhost:8000
```

No hay registro público: las cuentas se crean con el script.

### Variables de entorno

| Variable | Para qué |
|---|---|
| `DATABASE_URL` | Postgres. Esquema `postgresql+psycopg://` |
| `TOKEN_ENCRYPTION_KEY` | **Cifra** el token y las credenciales de LaLiga (Fernet). Si cambia, lo guardado deja de poder descifrarse |
| `SESSION_SECRET` | **Firma** la cookie de sesión. Rotarla solo cierra las sesiones abiertas |
| `FANTASY_CONTACTO` | Contacto que se incluye en el User-Agent al scrapear |

Genera las dos claves con:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Conectar tu cuenta de LaLiga

La app necesita un *bearer token* de LaLiga Fantasy, que **caduca cada 24 horas**. Dos vías:

- **Automática** (recomendada): en `/token`, introduces tu email y contraseña de LaLiga.
  Se guardan cifradas y la app renueva el token sola. Es lo que hace viable el job diario.
- **Manual**: pegas el token a mano en `/token`. Toca repetirlo cada día.

> La vía automática guarda tu contraseña de LaLiga **cifrada, pero de forma reversible**
> (hay que enviársela a LaLiga para renovar). Si la base de datos se compromete, esa
> cuenta también. Es una decisión consciente para que el cron funcione desatendido.

## Tests

```bash
uv run pytest              # 120 tests
```

Los tests corren contra el Postgres local, **nunca contra producción**: crean y borran
datos. Las fixtures salen de respuestas reales anonimizadas
(`scripts/generar_fixtures.py`).

## Despliegue

Tres piezas, todas en plan gratuito:

| Pieza | Dónde | Por qué |
|---|---|---|
| Web | Render (`render.yaml`) | Deploy automático desde `main` |
| Postgres | Neon | No caduca ni pausa por inactividad |
| Job diario | GitHub Actions | Render no incluye cron jobs en el plan free |

El job corre **dos veces** (16:30 y 17:30 UTC) a propósito: el cron se define en UTC y
Madrid alterna CET/CEST, así que una sola hora se desfasaría media temporada. El disparo
que cae antes del cierre no hace daño, porque el script tiene guardia horaria y es
idempotente por día.

Ningún secreto vive en el repo: en `render.yaml` van declarados con `sync: false` y sus
valores se cargan en el dashboard de Render y en los secrets de GitHub.

## Cómo desplegar

Se trabaja en `feature/v0.1.0`; `main` despliega a producción automáticamente, así que
solo recibe lo ya verificado.

**1. Comprobaciones automáticas**

```bash
bash scripts/pre_deploy.sh
```

Verifica rama, cambios sin commitear, tests en verde, migraciones pendientes en la base de
producción y que no haya secretos versionados.

**2. Ensayo contra la base de datos real** — el paso que más problemas evita:

```bash
uv run --env-file .env.produccion uvicorn fantasy.api.app:app --port 8010
```

Es la app local hablando con la base de producción. Detecta casi todo lo que fallaría en el
servidor (datos que en local no existen, migraciones a medias) sin desplegar nada.

**3. Si hay migraciones nuevas, aplícalas ANTES del merge**

```bash
uv run --env-file .env.produccion alembic upgrade head
```

El orden importa: si el código llega al servidor antes que la tabla, la web falla en
producción.

**4. Merge y despliegue**

```bash
git checkout main && git merge feature/v0.1.0 && git push
git checkout feature/v0.1.0
```

Render despliega solo. Si algo sale mal, en su panel de *Deploys* se puede volver al
anterior con un clic.

Los tests se ejecutan además automáticamente en cada push (GitHub Actions). No bloquean el
merge: con un único desarrollador basta con ver el resultado antes de mergear.

## Scripts

| Script | Para qué |
|---|---|
| `crear_usuario.py` | Alta manual de una cuenta |
| `guardar_credenciales_laliga.py` | Conectar una cuenta de LaLiga desde la terminal |
| `recon_oficial.py` | Guarda respuestas crudas de la API en `data/raw/` |
| `construir_mapa_equipos.py` | Regenera el puente de IDs de equipo entre fuentes |
| `construir_mapeo.py` | Empareja jugadores y reporta los no emparejados |
| `snapshot_diario.py` | El job del cron |
| `pre_deploy.sh` | Comprobaciones antes de desplegar |

## Sobre el scraping

Reglas que el código respeta y conviene mantener si lo reutilizas:

- Se comprueba `robots.txt` **antes de tocar cada ruta nueva**, no una vez por dominio.
- Peticiones **seriales y espaciadas** en segundos. Nunca concurrentes.
- User-Agent identificable, con nombre del proyecto y contacto.
- Caché local para no repetir peticiones innecesarias.

`analiticafantasy.com` se evaluó como segunda fuente y **se descartó**: sus datos solo
llegan por una ruta que su `robots.txt` prohíbe.
