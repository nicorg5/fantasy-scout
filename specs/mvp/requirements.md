# Requirements: MVP completo de fantasy-scout

Cada requirement es un comportamiento comprobable. Referencia al paso del MVP entre
corchetes al final. Ver decisiones en [`design.md`](./design.md).

Convención de verificación: `uv run ...` desde la raíz del repo; la app local en
`http://localhost:8000`.

---

## Bloque A — Scaffold y maqueta [paso 1]

- [x] **R1**: El repo tiene la estructura de carpetas acordada, y `data/raw/` y
  `data/cache/` están ignorados por git mientras `data/mappings/` sí se versiona.
  - Verificación: `git status --porcelain` no lista nada bajo `data/raw/` ni
    `data/cache/` tras crear un fichero de prueba en cada uno; `git check-ignore
    data/mappings/` sale con código 1 (no ignorado).

- [x] **R2**: `uv sync && uv run uvicorn fantasy.api.app:app` arranca sin errores y
  `GET /health` devuelve `200` con `{"status":"ok"}`.
  - Verificación: `curl -s -o /dev/null -w '%{http_code}' localhost:8000/health` → `200`.

- [x] **R3**: Existen las dos pantallas de la maqueta, navegables con **datos mock**, sin
  DB ni red externa: **mi plantilla** y **mercado**. Cada fila de jugador muestra su
  valor de mercado, su tendencia de valor y su probabilidad de jugar.
  - Verificación: `curl -s localhost:8000/plantilla` y `/mercado` devuelven `200` y el
    HTML contiene al menos un nombre de jugador mock y las tres métricas.

- [x] **R4**: La maqueta es **mobile-first**: cards apiladas en viewport estrecho, tabla
  en ancho. No hay ningún fichero `.js` propio ni build step.
  - Verificación: visual a 375px y a 1280px de ancho; `find . -name '*.js' -not -path
    './.venv/*'` no devuelve ficheros propios, y no existe `package.json`.

- [x] **R5**: Cada dato analítico renderizado lleva su **origen etiquetado** (oficial vs.
  scrapeado) y sabe renderizarse en estado **"no disponible"**.
  - Verificación: forzando el mock a `unavailable`, la fila muestra el badge de no
    disponible y **no** un `0`, un `-` ni un valor vacío que se confunda con dato real.

---

## Bloque B — Auth y token [paso 2]

- [x] **R6**: **No existe registro público**. *(Reescrito el 2026-09-27 por
  `specs/registro`)*: el alta es **por invitación de un solo uso**, que crea el admin en la
  pestaña Invitación; sin invitación válida, `POST /registro` responde 400 y no crea nada.
  `scripts/crear_usuario.py` queda como puerta trasera.
  - Verificación: `test_sin_invitacion_no_hay_alta` en `tests/test_auth.py`; el resto, en
    `specs/registro/requirements.md` (A7–A14).

- [x] **R7**: Un usuario creado puede hacer login y recibe una cookie de sesión firmada,
  `HttpOnly`, `Secure` y `SameSite=Lax`.
  - Verificación: `curl -i -X POST localhost:8000/login -d ...` devuelve `Set-Cookie` con
    los tres flags presentes.

- [x] **R8**: Las rutas `/plantilla` y `/mercado` exigen sesión: sin cookie válida
  redirigen a `/login`.
  - Verificación: `curl -s -o /dev/null -w '%{http_code}' localhost:8000/plantilla` → `302`
    hacia `/login`.

- [x] **R9**: La contraseña **nunca** se guarda en claro: la columna almacena un hash
  Argon2/bcrypt.
  - Verificación: `SELECT password_hash FROM "user"` devuelve un valor con prefijo de
    algoritmo (`$argon2` / `$2b$`), distinto de la contraseña introducida.

- [x] **R10**: Existe una pantalla donde el usuario pega su bearer token de LaLiga, **con
  instrucciones en pantalla** de cómo obtenerlo desde DevTools.
  - Verificación: `GET /token` devuelve `200`, contiene el formulario y el texto de
    instrucciones.

- [x] **R11**: El token se guarda **cifrado** con `TOKEN_ENCRYPTION_KEY` y con fecha de
  expiración.
  - Verificación: `SELECT token FROM user_token` devuelve ciphertext — el valor pegado no
    aparece como substring; la fila tiene `expires_at` no nulo.

- [x] **R12**: El token **nunca se expone al frontend ni a los logs**.
  - Verificación: `grep` del token pegado sobre el HTML de todas las páginas autenticadas
    y sobre la salida completa de los logs del servidor → 0 coincidencias.

- [x] **R13**: Si LaLiga responde `401` o el token está expirado, la UI pide repegar el
  token en vez de romper con un error genérico.
  - Verificación: con un token inválido a propósito, `/plantilla` devuelve `200` con el
    aviso y enlace a `/token`, no un `500`.

---

## Bloque C — Cliente API oficial [paso 3]

> Bloqueado por dato del usuario: HAR/cURL real de la sesión de LaLiga Fantasy.

- [x] **R14**: Un script de reconocimiento ejecuta las llamadas del HAR con el token real
  y **guarda las respuestas crudas** en `data/raw/`, con timestamp y nombre de endpoint.
  - Verificación: `ls data/raw/` muestra los ficheros JSON tras `uv run python
    scripts/recon_oficial.py`.

- [x] **R15**: Queda documentado en `design.md` (sustituyendo los `[SIN VERIFICAR]`) el
  host real y los endpoints reales de plantilla, liga y mercado.
  - Verificación: revisión del documento; no queda ningún `[SIN VERIFICAR]` del paso 3.

- [x] **R16**: El cliente `src/fantasy/official/` devuelve objetos tipados para plantilla
  y mercado, y **falla de forma explícita y legible** si la respuesta no tiene la forma
  esperada.
  - Verificación: test que alimenta al parser un JSON mutilado y espera una excepción
    propia con mensaje que nombre el campo ausente — no un `KeyError` pelado ni un `None`
    silencioso.

- [x] **R17**: El cliente nunca recibe el token como argumento desde una capa de
  presentación: lo obtiene descifrado desde el store del usuario.
  - Verificación: revisión de firma de funciones + `grep` de que ningún router pasa el
    token en claro.

---

## Bloque D — Scraping [paso 4]

> Bloqueado por dato del usuario/sesión: HTML real de futbolfantasy.com y
> analiticafantasy.com.

- [x] **R18**: Antes de scrapear cualquier ruta nueva se comprueba su `robots.txt`, y la
  comprobación queda registrada.
  - Verificación: el log de la ejecución incluye la línea de comprobación por ruta; existe
    un test que verifica que el scraper rechaza una ruta desautorizada.

- [x] **R19**: Las peticiones son **seriales, nunca concurrentes**, y espaciadas en
  segundos.
  - Verificación: los timestamps del log de una ejecución de N jugadores muestran
    separación ≥ el intervalo configurado y ningún solape.

- [x] **R20**: El User-Agent identifica el proyecto.
  - Verificación: `grep` del UA en el código; contiene el nombre del proyecto y un
    contacto.

- [x] **R21**: Existe caché local en `data/cache/`: una segunda ejecución dentro de la
  ventana de validez **no emite peticiones de red**.
  - Verificación: segunda ejecución consecutiva → 0 peticiones en el log y resultado
    idéntico.

- [x] **R22**: Para un jugador dado, el scraper devuelve **tendencia de valor** y
  **probabilidad de jugar**, cada una con su origen (qué sitio) y su timestamp.
  - Verificación: `uv run python scripts/scrape_jugador.py <nombre>` imprime ambos campos
    etiquetados.

- [x] **R23**: Si el sitio cambia y el parseo falla, el scraper devuelve
  **"no disponible"** con motivo, y **no lanza una excepción que suba a la petición web**.
  - Verificación: test que alimenta un HTML sin los selectores esperados y espera el
    resultado `unavailable`, no una excepción.

---

## Bloque E — Cruce de IDs [paso 5]

> **Punto más frágil del proyecto.** Ver §Riesgo de matching en `design.md`.

- [x] **R24**: Existe un proceso que, dados los jugadores en subasta del día (oficial) y
  los jugadores de los sitios scrapeados, produce un mapeo con **origen**
  (`heuristica` / `override_manual`) y **confianza** por cada emparejamiento.
  - Verificación: `uv run python scripts/construir_mapeo.py` produce el mapeo y un
    resumen: nº emparejados por heurística, por override, y nº **sin emparejar**.

- [x] **R25**: Los overrides manuales viven en un fichero **versionado** bajo
  `data/mappings/` y **tienen precedencia** sobre la heurística.
  - Verificación: añadir un override que contradiga a la heurística y comprobar que el
    mapeo resultante respeta el override.

- [x] **R26**: Un jugador **sin match** se sirve como analítica **"no disponible"**, nunca
  con datos de otro jugador ni con campos vacíos.
  - Verificación: forzar un jugador sin mapeo y comprobar que la fila sale con el badge de
    no disponible.

- [x] **R27**: Cada ejecución **registra el número de no-emparejados**, para que una
  subida brusca sea detectable como señal de rotura upstream.
  - Verificación: el contador aparece en el log/resumen de cada ejecución del snapshot.

- [x] **R28**: El algoritmo concreto de matching queda documentado en `design.md`
  sustituyendo su `[SIN VERIFICAR]`, incluyendo umbral de similitud y criterios de
  desempate.
  - Verificación: revisión del documento.

---

## Bloque F — Modelo de datos [paso 6]

- [x] **R29**: Existen las tablas `user`, `user_token`, `player`, `player_mapping` y
  `market_snapshot` en Postgres, creadas mediante migración versionada.
  - Verificación: `uv run alembic upgrade head` sobre una DB vacía termina en éxito y
    `\dt` lista las cinco tablas.

- [x] **R30**: `market_snapshot` tiene unicidad por (jugador, fecha local de Madrid), de
  forma que reejecutar el job del mismo día no duplica filas.
  - Verificación: ejecutar el job dos veces el mismo día → el `COUNT(*)` no cambia.

- [x] **R31**: Cada fila de `market_snapshot` distingue el **valor oficial** de los campos
  **analíticos scrapeados**, y estos últimos llevan marca de disponibilidad.
  - Verificación: inspección del esquema; existe la columna de estado de la analítica.

- [x] **R32**: La app funciona contra Postgres, no SQLite.
  - Verificación: `DATABASE_URL` con esquema `postgresql+...`; arranque en local contra
    Postgres correcto.

---

## Bloque G — Endpoints reales [paso 7]

- [x] **R33**: `GET /plantilla` devuelve la plantilla **real** del usuario autenticado
  (API oficial) enriquecida con la analítica scrapeada.
  - Verificación: con token válido, el HTML lista los jugadores reales de mi plantilla.

- [x] **R34**: `GET /mercado` devuelve las **subastas del día** reales enriquecidas con
  analítica.
  - Verificación: los jugadores mostrados coinciden con el mercado real de la liga ese día.

- [x] **R35**: **Degradación**: con el scraping caído por completo, `/plantilla` y
  `/mercado` siguen devolviendo `200` con los datos oficiales y la analítica marcada
  "no disponible".
  - Verificación: simular fallo total del scraper (host inalcanzable) → ambas rutas `200`,
    plantilla y mercado presentes, badges de no disponible en la analítica. **Este es el
    requirement que no se negocia.**

- [x] **R36**: El mock del paso 1 ya no se sirve en las rutas reales.
  - Verificación: `grep` de los nombres de jugador mock en las respuestas autenticadas →
    0 coincidencias.

---

## Bloque H — Frontend real [paso 8]

- [x] **R37**: Las pantallas del paso 1 consumen los datos reales del paso 7 sin cambiar
  el layout mobile-first ni introducir build step.
  - Verificación: mismas comprobaciones de R4 sobre las pantallas ya conectadas.

- [x] **R38**: Al menos una interacción se refresca con **htmx** sin recarga completa de
  página.
  - Verificación: la respuesta del endpoint parcial es un fragmento HTML, no una página
    completa, y el intercambio se ve en la pestaña de red.

- [x] **R39**: El origen del dato (oficial vs. scrapeado) es **visible para el usuario**
  en la pantalla, no solo en el payload.
  - Verificación: inspección visual de ambas pantallas.

---

## Bloque I — Cron [paso 9]

- [x] **R40**: Existe un script en `scripts/` que toma el snapshot diario de las subastas
  del día y lo persiste.
  - Verificación: `uv run python scripts/snapshot_diario.py` inserta filas en
    `market_snapshot` para la fecha de hoy.

- [x] **R41**: El job es **idempotente**: si ya existe snapshot para la fecha local de
  Madrid, no hace nada y termina con **código de salida 0**.
  - Verificación: segunda ejecución consecutiva → sin filas nuevas, exit code `0`.

- [x] **R42**: El job **no escribe nada si la hora local de Madrid es anterior a las
  18:00**, aunque el cron lo dispare.
  - Verificación: test con hora simulada a las 17:30 `Europe/Madrid` → 0 escrituras; a las
    18:30 → escribe.

- [x] **R43**: Las zonas horarias se resuelven con `zoneinfo`/`Europe/Madrid`, nunca con
  la hora local del contenedor.
  - Verificación: `grep` de `Europe/Madrid`; ausencia de `datetime.now()` sin tz en la
    ruta del job.

- [x] **R44**: El cron está configurado en Render con los dos disparos `30 16 * * *` y
  `30 17 * * *` UTC.
  - Verificación: dashboard de Render; una ejecución real termina en verde.

- [x] **R45**: Un fallo del scraping durante el job **no impide** guardar los valores
  oficiales del snapshot.
  - Verificación: ejecutar con el scraper caído → filas creadas con valor oficial y
    analítica marcada no disponible.

---

## Bloque J — Deploy [transversal]

- [x] **R46**: `DATABASE_URL`, `TOKEN_ENCRYPTION_KEY` y `SESSION_SECRET` viven **solo en
  el dashboard de Render**. `.env.example` documenta los nombres, jamás los valores.
  - Verificación: `grep -r` de los tres nombres en el repo solo los encuentra en
    `.env.example` y en `config.py`, nunca con valor real; `render.yaml` no los contiene.

- [x] **R47**: Push a `main` en GitHub dispara auto-deploy en Render (modo On Commit) y el
  servicio queda sirviendo la nueva versión.
  - Verificación: commit trivial → deploy verde en Render → `/health` responde en la URL
    pública.

- [x] **R48**: Ninguna migración se despliega en el mismo commit que el código que depende
  de ella.
  - Verificación: revisión del historial de commits — el commit de migración no toca el
    código que usa las columnas nuevas.

---

## Dependencias aún no disponibles

- **HAR/cURL real de la API oficial** — lo aporta el usuario. Bloquea el bloque C y, en
  cascada, E, G, H, I. Mientras tanto: se trabaja con el mock del bloque A.
- **HTML real de los sitios scrapeados** — se captura al llegar al paso 4. Bloquea el
  bloque D.
- **Bearer token válido del usuario** — necesario para cualquier verificación contra la
  API real (R33, R34, R40).
- **Esquema real de IDs de ambas fuentes** — bloquea el bloque E; no se resuelve con una
  heurística improvisada por adelantado.
- **Instancia de Postgres en Render + repo en GitHub** — bloquea el bloque J.
- **Histórico de mercado acumulado**: no es una dependencia real — por decisión de diseño
  la tendencia se lee de la fuente scrapeada, no se calcula sobre snapshots propios. La
  app no necesita esperar a acumular días para ser útil.

## Fuera de alcance (explícito)

- Puja recomendada.
- Registro público, recuperación de contraseña, verificación de email.
- Analítica de jugadores que no estén en las subastas del día.
- Multi-liga por usuario.
- Notificaciones de cualquier tipo.
- Build step de JS / SPA.
- Backup y restauración de la DB: es caché operativa, el histórico es re-obtenible.
