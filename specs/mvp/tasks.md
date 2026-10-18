# Tasks: MVP completo de fantasy-scout

Desglose de los **9 pasos acordados**, en su orden. Ninguna tarea se marca hecha sin su
verificación. Ver [`design.md`](./design.md) y [`requirements.md`](./requirements.md).

Leyenda de bloqueantes: **[DATO]** = necesita algo que solo puede aportar el usuario.

---

## Paso 1 — Scaffold + maqueta navegable con datos mock

- [x] **T1.1**: Crear repo: `pyproject.toml` (uv), `.gitignore`, `.env.example`,
  `README.md`, y el árbol `src/fantasy/{config,auth,official,scrapers,matching,storage,analytics,api}`
  + `data/{raw,cache,mappings}/` + `scripts/`. `data/raw/` y `data/cache/` ignorados;
  `data/mappings/` versionado con un `.gitkeep`.
  - Bloqueante: ninguno.
  - Verificación: **R1** — fichero de prueba en `data/raw/` y `data/cache/` no aparece en
    `git status`; `git check-ignore data/mappings/` sale con código 1.

- [x] **T1.2**: App FastAPI mínima en `src/fantasy/api/app.py` con `GET /health`, y
  `config.py` leyendo entorno con defaults locales.
  - Bloqueante: ninguno.
  - Verificación: **R2** — `curl -o /dev/null -w '%{http_code}' localhost:8000/health` → `200`.

- [x] **T1.3**: Definir los **tipos de dominio de presentación** (jugador con valor
  oficial + bloque analítico separado que admite estado `unavailable`) y un módulo de
  datos mock que los produzca. Este contrato es el que los pasos 7-8 rellenarán con datos
  reales, así que se fija aquí.
  - Bloqueante: ninguno.
  - Verificación: un test construye un jugador mock con analítica `unavailable` y otro con
    analítica presente; ambos son válidos.

- [x] **T1.4**: Templates Jinja2 + Pico.css: layout base, `/plantilla` y `/mercado` con
  los datos mock. Cada fila muestra valor, tendencia y probabilidad de jugar, **con el
  origen etiquetado**.
  - Bloqueante: ninguno.
  - Verificación: **R3** — ambas rutas `200` y el HTML contiene un jugador mock y las tres
    métricas.

- [x] **T1.5**: Estilos mobile-first: cards apiladas en estrecho, tabla en ancho. Sin
  `package.json` ni `.js` propio.
  - Bloqueante: ninguno.
  - Verificación: **R4** — inspección a 375px y 1280px; `find` no encuentra JS propio.

- [x] **T1.6**: Componente/partial de **"analítica no disponible"** (badge), usado por
  ambas pantallas.
  - Bloqueante: ninguno.
  - Verificación: **R5** — forzando el mock a `unavailable` sale el badge, **no** un `0`
    ni un `-`.

> **Hito 1**: maqueta navegable end-to-end sin DB ni red. Es lo que permite validar la UI
> antes de depender del HAR y del scraping.

---

## Paso 2 — Auth propia + almacenamiento cifrado del token

- [x] **T2.1**: `storage/`: engine + sesión SQLAlchemy 2.0 contra Postgres, y Alembic
  inicializado.
  - Bloqueante: Postgres local corriendo.
  - Verificación: **R32** — `alembic upgrade head` en vacío termina en éxito con
    `DATABASE_URL` `postgresql+...`.

- [x] **T2.2**: Modelos `user` y `user_token` + migración.
  - Bloqueante: T2.1.
  - Verificación: `\dt` lista ambas tablas.

- [x] **T2.3**: Hashing de password (Argon2/bcrypt vía passlib) y
  `scripts/crear_usuario.py` para el **alta manual**. Sin endpoint de registro.
  - Bloqueante: T2.2.
  - Verificación: **R6**, **R9** — `POST /registro` → `404`; el script crea la fila; el
    hash almacenado tiene prefijo de algoritmo y no es la contraseña.

- [x] **T2.4**: Login/logout con sesión por cookie firmada con `SESSION_SECRET`
  (`HttpOnly`, `Secure`, `SameSite=Lax`), y dependencia de FastAPI que protege rutas.
  - Bloqueante: T2.3.
  - Verificación: **R7**, **R8** — `Set-Cookie` con los tres flags; `/plantilla` sin
    cookie → `302` a `/login`.

- [x] **T2.5**: Cifrado del token con `TOKEN_ENCRYPTION_KEY` (Fernet) y store que
  guarda/lee/descifra con expiración. Descifrado solo en memoria en el servidor.
  - Bloqueante: T2.2.
  - Verificación: **R11** — el valor pegado no aparece como substring de la columna;
    `expires_at` no nulo.

- [x] **T2.6**: Pantalla `GET/POST /token` con el formulario y **las instrucciones en
  pantalla** de cómo sacar el bearer de DevTools.
  - Bloqueante: T2.5.
  - Verificación: **R10**, **R12** — la página contiene las instrucciones; `grep` del
    token sobre el HTML de todas las páginas autenticadas y sobre los logs → 0
    coincidencias.

> **Hito 2**: puedo crear mi cuenta, entrar y guardar mi token cifrado. La maqueta ya está
> detrás de login.

---

## Paso 3 — Cliente contra la API oficial (reconocimiento)

- [x] **T3.0**: **[DATO]** Capturar el HAR/cURL real de una sesión de LaLiga Fantasy
  (plantilla, liga, mercado) y dejarlo en `data/raw/`. Aportado por el usuario.
  - Bloqueante: **solo el usuario puede hacer esto.** Bloquea todo el paso 3 y, en
    cascada, los pasos 5, 7, 8 y 9.
  - Verificación: el fichero existe y contiene las tres llamadas.
  - **Resuelto por otra vía**: el HAR capturado (`data/raw/127.0.0.1.har`) resultó
    insuficiente — solo veía el proxy local de la app de terceros, sin host real ni
    cabecera `Authorization`. El reconocimiento se completó leyendo el **código** de ese
    cliente open-source, y se cerró con **llamadas reales propias** contra la API usando
    el token del usuario (`data/raw/token.txt`, gitignored). El objetivo de la tarea
    —conocer host, rutas y cabeceras reales— está cumplido y verificado. Ver
    §Descubrimientos.

- [x] **T3.1**: `scripts/recon_oficial.py`: reproduce las llamadas del HAR con el token
  real y **guarda las respuestas crudas** con timestamp y nombre de endpoint.
  - Bloqueante: T3.0 + token válido en DB.
  - Verificación: **R14** — `ls data/raw/` muestra los JSON crudos.

- [x] **T3.2**: Fijar en `design.md` el **host real** y los endpoints reales, sustituyendo
  los `[SIN VERIFICAR]` del paso 3.
  - Bloqueante: T3.1.
  - Verificación: **R15** — no queda ningún `[SIN VERIFICAR]` del paso 3 en el documento.

- [x] **T3.3**: Cliente `official/` con modelos tipados de plantilla y mercado, y
  **validación explícita de forma**: excepción propia que nombre el campo ausente.
  - Bloqueante: T3.2.
  - Verificación: **R16** — test con JSON mutilado espera la excepción propia, no un
    `KeyError` ni un `None` silencioso.

- [x] **T3.4**: El cliente obtiene el token descifrado del store; ningún router se lo pasa
  en claro.
  - Bloqueante: T3.3, T2.5.
  - Verificación: **R17** — revisión de firmas + `grep`.

- [x] **T3.5**: Manejo de `401`/token expirado como error de dominio propio, para que la
  UI del paso 7 pueda pedir repegar el token.
  - Bloqueante: T3.3.
  - Verificación: test que simula `401` y espera el error de dominio, no un `500`.

> **Hito 3**: puedo leer mi plantilla y el mercado reales desde Python. Todavía no en la web.

---

## Paso 4 — Scraper de tendencia de valor y probabilidad de jugar

> Independiente del paso 3: **se puede hacer en paralelo** si el HAR se retrasa.

- [x] **T4.1**: Utilidad de `robots.txt`: comprueba y **registra** el permiso por ruta
  antes de tocarla; rechaza rutas desautorizadas.
  - Bloqueante: ninguno.
  - Verificación: **R18** — test que verifica el rechazo de una ruta desautorizada; la
    comprobación aparece en el log.

- [x] **T4.2**: Cliente HTTP base de scraping: **serial, nunca concurrente**, espaciado en
  segundos configurable, User-Agent con nombre del proyecto y contacto.
  - Bloqueante: ninguno.
  - Verificación: **R19**, **R20** — timestamps del log con separación ≥ intervalo y sin
    solape; `grep` del UA.

- [x] **T4.3**: Caché local en `data/cache/` con ventana de validez.
  - Bloqueante: T4.2.
  - Verificación: **R21** — segunda ejecución consecutiva emite 0 peticiones.

- [x] **T4.4**: **[DATO]** Capturar HTML real de futbolfantasy.com y analiticafantasy.com
  para un jugador conocido, guardado como fixture de test.
  - Bloqueante: requiere T4.1 en verde (no se toca una ruta sin comprobar robots).
  - Verificación: los fixtures existen en el repo de tests.

- [x] **T4.5**: Parsers de **tendencia de valor** y **probabilidad de jugar**, cada
  resultado con su sitio de origen y timestamp. Fijar en `design.md` la forma real de los
  datos scrapeados (sustituye el `[SIN VERIFICAR]` del paso 4).
  - Bloqueante: T4.4.
  - Verificación: **R22** — `scripts/scrape_jugador.py <nombre>` imprime ambos campos
    etiquetados.

- [x] **T4.6**: **Degradación**: parseo fallido devuelve `unavailable` **con motivo**, sin
  lanzar excepción hacia arriba.
  - Bloqueante: T4.5.
  - Verificación: **R23** — test con HTML sin los selectores esperados espera
    `unavailable`, no excepción.

> **Hito 4**: para un jugador dado por nombre, tengo tendencia y probabilidad, y el fallo
> está contenido.

---

## Paso 5 — Cruce de IDs entre API oficial y sitios scrapeados

> **Punto más frágil del proyecto.** Ninguna heurística se da por buena en silencio.

- [x] **T5.1**: Volcar los identificadores reales de ambos lados (oficial vs. scrapeado) y
  **diagnosticar** qué campos hay en común de verdad (nombre, equipo, posición, dorsal…).
  Sin escribir aún el algoritmo.
  - Bloqueante: T3.1 (respuestas crudas oficiales) + T4.4 (HTML real). **[DATO]**
  - Verificación: un documento corto o sección en `design.md` que liste los campos
    disponibles a cada lado y cuáles son comparables.

- [x] **T5.2**: **Decidir con el usuario** el algoritmo: normalización de nombres,
  criterios de desempate, umbral de similitud. Documentarlo en `design.md` sustituyendo el
  `[SIN VERIFICAR]` del paso 5. **Señalar el riesgo explícitamente.**
  - Bloqueante: T5.1. **Requiere conversación con el usuario, no se decide en solitario.**
  - Verificación: **R28** — el documento describe umbral y desempates.

- [x] **T5.3**: Modelos `player` y `player_mapping` (origen `heuristica`/`override_manual`
  + confianza) y su migración.
  - Bloqueante: T5.2.
  - Verificación: `\dt` lista ambas; el esquema tiene las columnas de origen y confianza.

- [x] **T5.4**: `matching/`: implementación del algoritmo + carga del fichero de
  **overrides versionado** en `data/mappings/`, con **precedencia del override**.
  - Bloqueante: T5.3.
  - Verificación: **R25** — un override que contradice la heurística gana.

- [x] **T5.5**: `scripts/construir_mapeo.py` con **resumen de ejecución**: emparejados por
  heurística, por override, y **nº sin emparejar**.
  - Bloqueante: T5.4.
  - Verificación: **R24**, **R27** — el script imprime el resumen con el contador de
    no-emparejados.

- [x] **T5.6**: Sin match ⇒ analítica `unavailable`, nunca datos de otro jugador ni campos
  vacíos.
  - Bloqueante: T5.4.
  - Verificación: **R26** — jugador forzado sin mapeo sale con el badge.

- [x] **T5.7**: Revisión manual de los no-emparejados del día y volcado a
  `data/mappings/`. Viable porque el mercado son decenas de jugadores, no cientos.
  - Bloqueante: T5.5.
  - Verificación: tras la revisión, el nº de no-emparejados baja y el fichero está
    commiteado.

> **Hito 5**: puedo unir un jugador oficial con su analítica scrapeada, con los fallos
> visibles y corregibles a mano.

---

## Paso 6 — Modelo de datos (snapshots diarios)

- [x] **T6.1**: Modelo `market_snapshot` (fecha local Madrid, jugador, **valor oficial**
  separado de los **campos analíticos** con su marca de disponibilidad) + migración.
  - Bloqueante: T5.3.
  - Verificación: **R29**, **R31** — la tabla existe; el esquema separa oficial de
    analítico y tiene columna de estado.

- [x] **T6.2**: Restricción de **unicidad por (jugador, fecha local Madrid)**.
  - Bloqueante: T6.1.
  - Verificación: **R30** — insertar dos veces el mismo día no duplica.

- [x] **T6.3**: Purga perezosa de snapshots más antiguos que la retención configurada
  (por defecto 90 días).
  - Bloqueante: T6.1.
  - Verificación: con retención a 1 día y una fila antigua sembrada, la purga la elimina.

---

## Paso 7 — Endpoints FastAPI con datos reales

- [x] **T7.1**: Capa de servicio que compone **plantilla oficial + analítica**,
  rellenando el contrato fijado en T1.3.
  - Bloqueante: T3.3, T4.5, T5.4.
  - Verificación: test de la composición con oficial real y analítica mockeada.

- [x] **T7.2**: `GET /plantilla` sirviendo datos reales del usuario autenticado.
  - Bloqueante: T7.1.
  - Verificación: **R33** — el HTML lista los jugadores reales de mi plantilla.

- [x] **T7.3**: `GET /mercado` sirviendo las **subastas del día** reales enriquecidas.
  - Bloqueante: T7.1.
  - Verificación: **R34** — coinciden con el mercado real de la liga ese día.

- [x] **T7.4**: **Degradación end-to-end**: scraping caído por completo ⇒ ambas rutas
  siguen en `200` con datos oficiales y analítica `unavailable`.
  - Bloqueante: T7.2, T7.3.
  - Verificación: **R35** — con el host del scraper inalcanzable, ambas rutas `200` con
    los badges. **Este es el requirement que no se negocia.**

- [x] **T7.5**: Manejo de token inválido/expirado en la UI: aviso + enlace a `/token`, no
  un `500`.
  - Bloqueante: T3.5, T7.2.
  - Verificación: **R13** — con token inválido, `/plantilla` → `200` con el aviso.

- [x] **T7.6**: Retirar el mock de las rutas reales.
  - Bloqueante: T7.2, T7.3.
  - Verificación: **R36** — `grep` de nombres mock en respuestas autenticadas → 0.

---

## Paso 8 — Frontend conectado a datos reales

- [x] **T8.1**: Ajustar los templates del paso 1 a los datos reales **sin cambiar el
  layout mobile-first** ni introducir build step.
  - Bloqueante: T7.2, T7.3.
  - Verificación: **R37** — repetir las comprobaciones de R4 sobre las pantallas
    conectadas.

- [x] **T8.2**: Una interacción refrescada con **htmx** vía endpoint que devuelve
  **fragmento** HTML, no página completa.
  - Bloqueante: T8.1.
  - Verificación: **R38** — la respuesta del parcial es un fragmento; el intercambio se ve
    en la pestaña de red.

- [x] **T8.3**: El **origen del dato** (oficial vs. scrapeado) es visible en pantalla, no
  solo en el payload.
  - Bloqueante: T8.1.
  - Verificación: **R39** — inspección visual de ambas pantallas.

> **Hito 8**: la web es usable de verdad desde el móvil con datos reales.

---

## Paso 9 — Cron del snapshot diario en Render

- [x] **T9.1**: `scripts/snapshot_diario.py`: toma las subastas del día, resuelve el
  matching, scrapea la analítica y persiste en `market_snapshot`.
  - Bloqueante: T6.1, T7.1.
  - Verificación: **R40** — inserta filas para la fecha de hoy.

- [x] **T9.2**: **Guardia horaria**: no escribe nada si la hora local de Madrid es anterior
  a las 18:00, aunque el cron lo dispare. Zonas con `zoneinfo`/`Europe/Madrid`, nunca la
  hora del contenedor.
  - Bloqueante: T9.1.
  - Verificación: **R42**, **R43** — hora simulada 17:30 → 0 escrituras; 18:30 → escribe;
    `grep` sin `datetime.now()` sin tz en la ruta del job.

- [x] **T9.3**: **Idempotencia**: si ya hay snapshot de la fecha local de hoy, no hace nada
  y sale con **código 0**.
  - Bloqueante: T9.1, T6.2.
  - Verificación: **R41** — segunda ejecución sin filas nuevas y exit code `0`.

- [x] **T9.4**: Fallo de scraping durante el job **no impide** guardar los valores
  oficiales.
  - Bloqueante: T9.1.
  - Verificación: **R45** — con el scraper caído, filas creadas con valor oficial y
    analítica no disponible.

- [x] **T9.5**: Log del job con el **contador de no-emparejados** del matching, como señal
  de rotura upstream.
  - Bloqueante: T9.1, T5.5.
  - Verificación: **R27** — el contador aparece en la salida de cada ejecución.

- [x] **T9.6**: Configurar el cron en Render con **`30 16 * * *` y `30 17 * * *` UTC**
  (doble disparo idempotente, ver §Cron en `design.md`).
  - Bloqueante: T9.3 (la idempotencia debe existir **antes** de programar dos disparos),
    T10.2.
  - Verificación: **R44** — dashboard de Render; una ejecución real termina en verde.

---

## Transversal — Deploy

> T10.1 y T10.2 conviene hacerlas pronto (tras el Hito 1) para desplegar temprano y no
> descubrir problemas de entorno al final. El resto es disciplina continua.

- [x] **T10.1**: Repo en GitHub + servicio en Render conectado a `main` con auto-deploy
  **On Commit**, y Postgres gestionada de Render.
  - Bloqueante: T1.2 (algo que desplegar).
  - Verificación: **R47** — commit trivial → deploy verde → `/health` responde en la URL
    pública.

- [x] **T10.2**: `DATABASE_URL`, `TOKEN_ENCRYPTION_KEY` y `SESSION_SECRET` cargadas **en
  el dashboard de Render**; `.env.example` con los nombres y **jamás** los valores.
  - Bloqueante: T10.1.
  - Verificación: **R46** — `grep -r` de los tres nombres solo los encuentra en
    `.env.example` y `config.py`; `render.yaml` no los contiene.

- [x] **T10.3**: Disciplina de migraciones: **ninguna migración viaja en el mismo commit
  que el código que la usa**. Aplica a T2.2, T5.3, T6.1.
  - Bloqueante: continuo.
  - Verificación: **R48** — el commit de migración no toca el código que usa las columnas
    nuevas.

---

## Camino crítico y paralelizable

- **Bloqueado por [DATO] del usuario**: T3.0 (HAR) bloquea el paso 3 y, en cascada, 5, 7,
  8 y 9. Es la dependencia más cara del proyecto — conviene capturarla pronto.
- **Se puede avanzar sin el HAR**: paso 1 entero, paso 2 entero, paso 4 hasta T4.6, y
  T10.1-T10.2. Es bastante trabajo: si el HAR se retrasa, no hay motivo para pararse.
- **T5.2 requiere conversación**, no solo código: es la decisión que el design deja
  deliberadamente abierta.

## Estado: MVP COMPLETO (2026-08-28)

Las 52 tareas y los 48 requirements verificados. En produccion:

| Pieza | Donde | Estado |
|---|---|---|
| Web | Render free | https://fantasy-scout-e1ll.onrender.com |
| Postgres | Neon | 8 tablas, 669 filas de analitica |
| Cron | GitHub Actions | 16:30 y 17:30 UTC, validado a mano |

**Pendiente de observar**: el primer disparo automatico del cron (2026-08-28, 18:30 hora
de Madrid). Conviene revisar Actions al dia siguiente para confirmar que el ciclo funciona
desatendido.

## Descubrimientos

### Post-MVP — identidad visual de LaLiga Fantasy (2026-09-09)

- **La mayoría de los colores de `fantasy.laliga.com` son Bootstrap 5, no LaLiga.** Sacar
  los hex por frecuencia daba `#0d6efd`, `#dc3545`, `#198754`... que son los defaults de
  Bootstrap. La marca de verdad solo aparece al restar esos: `#11111f`, `#232433`,
  `#353542` y el acento `#ff4b44`. Ver design.md §Identidad visual.
- **Su gris tenue no cumple accesibilidad en nuestro contexto**: `#7b7b8c` da 3,68:1
  sobre las cards, por debajo del 4,5:1 de WCAG AA. Se aclaró a `#9a9aad`. Copiar una
  marca no exime de medir el contraste en el sitio donde la vas a usar.
- **Los filtros de Clausulazos ya no dejan la pantalla muda**: el formulario GET pasa por
  `hx-boost`, que permite mostrar "Filtrando…" sin perder la URL compartible. Mismo
  mecanismo que la navegación entre secciones — no hizo falta inventar nada nuevo.
- **La revisión visual se hizo sin desplegar.** Se renderizaron las tres pantallas reales
  con el servicio mockeado (igual que los tests) y se publicaron como vista previa, y
  luego se validó desde el móvil levantando la app en local **contra Neon**
  (`--env-file .env.produccion`, puerto 8010 del portproxy). Evita el ciclo de
  push → deploy → cold start de ~2 min por cada iteración de diseño.
- **Detalle que casi rompe esa prueba**: `.env.produccion` no define `FANTASY_ENTORNO`,
  así que se hereda `local` de `.env` y la cookie de sesión no lleva `Secure`. Si lo
  definiera, el login desde el móvil por HTTP plano fallaría sin explicación.

### Post-MVP — probabilidad y botón "Actualizar datos" (2026-09-02)

- **Bug real de cobertura en la probabilidad de jugar**, no una limitación del sitio: el
  parser usaba el widget viejo de la página de equipo (`a.camiseta[data-probabilidad]`,
  ~20/26 del equipo) en vez del widget completo del "campo" (`div.jugador_{id} >
  span.probabilidad-widget`, 26/26). Detectado porque el usuario reportó que Huijsen y
  O. Rey (titulares) no salían — ver design.md §Cobertura real de cada métrica para el
  detalle técnico y la verificación con datos reales.
- De paso, ese `{id}` resultó ser el **mismo id numérico** que usa la tabla de mercado:
  el cruce entre páginas pasó de nombre-con-tolerancia a id-exacto, simplificando bastante
  `analytics/servicio.py` (se eliminó `SequenceMatcher`/fallback por apellido).
- **Botón "Actualizar datos" ahora scrapea en vivo** (antes solo releía `analitica_diaria`,
  igual que la carga normal de página): decisión consciente del usuario de reabrir el
  problema de rendimiento de forma acotada — solo a los equipos de esa pantalla, y solo
  en ese endpoint. Ver design.md §Botón "Actualizar datos" para el detalle y las cifras
  reales medidas (7 s con caché tibia, ~60 s forzando red real en el peor caso).
- **La caché de scraping de 6h no distinguía el origen de la petición**: sin
  `ignorar_cache`, el botón podía devolver el mismo HTML que ya tenía el cron. Se añadió
  el parámetro explícito en `ClienteScraping.get_html` en vez de bajar la ventana global,
  para no perder la protección de caché en el resto de casos (cron, tests).
- **"Cargando…" al moverse entre secciones** se resolvió con `hx-boost` en la navegación
  del header, no con JS propio: coherente con R4/R37 (sin build step).

### Deploy (2026-08-28)

- **El plan free de Render no sostiene el diseno original**: no tiene cron jobs y su
  Postgres caduca a los 30 dias. Se repartió en tres piezas gratuitas (Render + Neon +
  GitHub Actions) en vez de pagar ~14 $/mes. Ver design.md.
- **Neon elegido por no pausar por inactividad**, no por tamano: Supabase pausa tras una
  semana sin uso, justo el patron de este proyecto. El uso real es <1 MB.
- **Los tests corrieron una vez contra Neon** (51 s en vez de 5) antes de separar
  entornos. Lo grave no era la lentitud: la suite crea y borra datos.
- **Un `=` perdido al copiar la clave Fernet** dejo `TOKEN_ENCRYPTION_KEY` invalida en
  local y en Render a la vez. Falla con "Incorrect padding". Al copiar claves base64, el
  `=` final se pierde con facilidad porque parece decorativo.
- **La IP de Windows cambio por DHCP** (`.113` -> `.105`) durante la sesion, y el acceso
  desde el movil dejo de funcionar. El portproxy y el firewall estaban bien: era la IP.
  Primer sitio donde mirar si un dia deja de cargar.
- **Verificacion cruzada del cron**: el log de GitHub Actions mostro `13:01 UTC` traducido
  a `15:01 CEST` antes de negarse a escribir. Eso valido de una sola pasada los secrets,
  la conversion de zona horaria en un servidor UTC, y la guardia horaria.
- **Aviso conocido y benigno**: `equipo 671 sin slug conocido`. Es un equipo que aparece
  en la tabla de futbolfantasy pero no esta en LaLiga Fantasy. No afecta.
- **Se commiteo en `main` sin avisar** tras un merge de despliegue. No tuvo consecuencias
  (fast-forward), pero llevo a fijar por escrito el flujo de ramas en design.md.

### Login automático contra LaLiga (2026-08-28) — cambio de decisión

- **Se descubrió el flujo de autenticación completo** en el bundle de
  `Externoak/LaLigaApp` v3.5.3: Azure B2C con **grant_type=password (ROPC)**, el mismo que
  usa la app oficial. `EMAIL_CLIENT_ID` coincide exactamente con el `aud` del JWT del
  usuario, lo que confirma que es el flujo real y no un rodeo.
- **Verificado antes de construir nada**: con credenciales falsas el endpoint responde
  `AADB2C90225` / HTTP 400, la forma correcta de rechazo.
- **Motivo del cambio**: el token dura 24 h, así que pegarlo a mano hace **inviable el cron
  del paso 9** — un job nocturno no puede depender de que alguien haya pegado un token esa
  tarde. Esto no se había visto al escribir el design.
- **El usuario eligió la opción A (guardar email + contraseña cifrados)** con los riesgos
  expuestos por escrito antes de decidir. Sustituye a dos decisiones cerradas del design:
  "no hay OAuth propio contra LaLiga" y "no se intenta refrescar automáticamente".
- **La renovación va de lo barato a lo caro**: token vigente → `refresh_token` (sin
  credenciales) → login completo. La contraseña se usa lo mínimo posible.
- **Las credenciales se validan ANTES de guardarse**: persistir algo que no funciona solo
  retrasa el fallo hasta que lo sufra el cron.
- **Un rechazo de credenciales no se reintenta**: repetir con credenciales malas no arregla
  nada y machaca el login de LaLiga. Se registra en `ultimo_error` para poder avisar.
- **Los errores de red no incluyen el cuerpo de la petición**, que llevaría la contraseña.
  Hay un test que lo comprueba explícitamente.
- **Guardar credenciales es opcional**: la vía manual de pegar el token sigue existiendo
  como respaldo, por si LaLiga cierra el ROPC. Hay botón para desconectar la cuenta.
- **Cobertura real con datos del 2026-08-28**: mercado 12/13 con analítica (1 entrenador
  excluido), plantilla **12/12**. Mejor que el 7/11 del día anterior — la cobertura de
  probabilidad varía según quién esté en el once probable ese día.

### Paso 7 — endpoints con datos reales (2026-08-27)

- **Bug real: la página de equipo se direcciona por SLUG, no por el id numérico.** La
  tabla de mercado identifica al equipo con `data-equipo="11"`, pero la URL es
  `/laliga/equipos/malaga`. Usar el número daba 404 en los 10 equipos. La degradación
  funcionó (no rompió nada, marcó "no disponible"), pero el dato no llegaba: **un fallo
  silencioso que solo se vio probando con datos reales**, no en los tests. El mapa de
  equipos guarda ahora id **y** slug, ambos extraídos del propio HTML.
- **Las dos páginas del mismo sitio no comparten clave**: la tabla de mercado usa un id
  numérico y la de equipo un slug, y sus ids de jugador son esquemas distintos
  (`data-id="7614"` vs `class="jugador_15344"`). El cruce entre ambas se hace por nombre.
- **futbolfantasy pierde la primera letra del nombre al generar slugs si va acentuada**:
  `Ángel Ortiz` → `ngel-ortiz`, `Álvaro Núñez` → `lvaro-nunez`. Es un defecto suyo, no
  nuestro. Por eso el cruce de slug usa similitud (umbral 0,85) y no igualdad: recuperó 2
  jugadores. También hay sufijos de desambiguación (`dani-lorenzo-1`).
- **Las dos métricas NO tienen la misma cobertura**, y es estructural: la tendencia sale de
  la tabla de mercado (~669 jugadores) y la probabilidad de la página de alineación de cada
  equipo (solo el once probable). 11/11 jugadores de campo tienen tendencia; 7/11 tienen
  además probabilidad. Los 4 restantes no están en el once probable **ni tienen el dato en
  su ficha individual** — comprobado uno a uno, no es un fallo del scraper.
- **`analiticafantasy.com` evaluado como segunda fuente y descartado**: su página de
  alineaciones es React y el HTML servido **no contiene ningún dato de jugador** (0
  apariciones de nombres conocidos); todo viene de `/api/`, que su `robots.txt` prohíbe.
  Un navegador headless acabaría pegando a esa misma ruta, así que se descarta por
  respetar robots.txt, no por dificultad técnica.
- **Cambio de contrato acordado con el usuario**: cada métrica del bloque analítico tiene
  disponibilidad propia. Antes se exigían ambas, lo que descartaba tendencia real de 4 de
  11 jugadores. La invariante que se mantiene: un dato ausente es `None` + motivo visible,
  nunca un 0 ni un guion mudo. En la UI, "sin dato" con su motivo, distinto del badge de
  analítica caída entera.
- **La liga y el equipo del usuario se consultan a `/leagues`, no se guardan en nuestra
  base de datos**: 1 KB por request, evita estado duplicado que sincronizar, y si el
  usuario cambia de liga funciona solo.
- **El token caducó a mitad del paso 7 y el sistema lo detectó solo** (`TokenInvalido`,
  0,9 h después de expirar). Validación no planeada de la caducidad de 24 h descubierta en
  el paso 3.

### Paso 5 — cruce de IDs (2026-08-27)

- **Lo que NO funciona, medido sobre 649 scrapeados vs. 804 oficiales**: cruzar por valor
  de mercado exacto acierta un **17,4%** (y 86 de 113 coincidencias eran ambiguas, varios
  jugadores con el mismo valor); cruzar por nombre sin acotar equipo, un **27,4%**
  (futbolfantasy usa nombre completo, `jon karrikaburu`; LaLiga usa apodo, `Karrikaburu`).
  Ambas descartadas con datos, no por intuición.
- **El puente de equipos es lo que hace viable el resto.** Los IDs de equipo no coinciden
  (Málaga = `12` oficial, `11` en futbolfantasy) y **ambos son enteros pequeños**: cruzarlos
  directamente no da error, simplemente empareja equipos equivocados en silencio. Es el
  fallo más peligroso posible aquí.
- **El mapa de equipos se dedujo por votación**, no a mano: se toman los jugadores que
  casan por nombre exacto e inequívoco y se cuenta a qué equipo apuntan en ambos lados.
  **20/20 equipos, votación unánime, 0 conflictos**, con 181 votos. Versionado en
  `data/mappings/equipos.json` y regenerable con `scripts/construir_mapa_equipos.py`.
- **Caso peligroso real, resuelto por el filtro de equipo**: `Alfon` (oficial, Sevilla)
  tenía dos candidatos por nombre — `alfonso gonzalez` y `alfonso herrero`. Alfonso Herrero
  es del Málaga **y está en la plantilla del usuario**: emparejarlo mal habría contaminado
  un dato que él mira a diario, sin forma de notarlo. Acotar por equipo lo descarta solo.
- **Los entrenadores usan IDs con otro formato en futbolfantasy** (`data-id="e119"`, no
  numérico). Un `grep` con `data-id="(\d+)"` los excluye en silencio — pasó durante el
  diagnóstico y falseó los primeros resultados. El parser real usa `[data-id]` sin exigir
  dígitos, así que sí los captura. Quedan fuera del MVP por decisión del usuario.
- **Ninguna de las tres formas del nombre oficial gana siempre**: `Larrubia` solo casa por
  `name` (`David Larrubia`), `Llorenç` solo por `slug` (`llorenc-serred`). Por eso se
  comparan las tres variantes (`name`, `nickname`, `slug`) contra el candidato.
- **Salvaguarda de ambigüedad añadida por iniciativa propia** (no la pidió el usuario):
  con umbral permisivo (0,60), dos candidatos del mismo equipo a menos de 0,05 de
  similitud entre sí se tratan como *sin match* en vez de elegir el mejor. Sin esto, un
  umbral bajo puede emparejar al azar dos apellidos parecidos del mismo equipo.
- **Resultado sobre el mercado real**: 11 jugadores (excluidos 2 entrenadores),
  **11 emparejados, 0 sin emparejar**. Peor confianza: 0,91 (`Galdin` ↔ `igor galdin`,
  transliteración Ihor/Igor). Los 11 verificados a mano como correctos.
- **`data/mappings/overrides.json` ignora las claves que empiezan por `_`**: JSON no admite
  comentarios y el fichero se autodocumenta con entradas `_comentario` / `_ejemplo`. La
  primera versión las cargaba como overrides reales.

### Paso 4 (2026-08-27)

- **`robots.txt` de futbolfantasy.com permite todo** (`Disallow:` vacío) para las rutas
  que necesitamos. Comprobado con el UA real antes de tocar nada.
- **Los datos vienen en atributos `data-*` estructurados, no en texto libre** — mucho más
  estable ante rediseños visuales que parsear texto renderizado:
  - Tendencia: `<tr class="elemento_jugador" data-id data-nombre data-diferencia1 ...>`
    en `/analytics/laliga-fantasy/mercado`. `data-diferencia1` es la variación en euros
    de las últimas 24h — encaja con la cadencia diaria del snapshot. Hay también
    `data-diferencia2/3/7/14/30`, sin usar en el MVP pero ahí si hiciera falta ventana
    distinta.
  - Probabilidad de jugar: `<a class="camiseta" data-probabilidad="NN%" href=".../
    jugadores/{slug}">` en `/laliga/equipos/{slug-equipo}`. El propio sitio lo describe
    como "probabilidad de ser titular la próxima jornada" en un modal de ayuda — es
    justo el dato que buscábamos, no una aproximación.
- **futbolfantasy.com cubre las dos métricas** (tendencia y probabilidad); no hizo falta
  analiticafantasy.com para el MVP. Confirmado además que LaLigaApp tampoco lo usa.
- **Los slugs de equipo de futbolfantasy.com NO son los de LaLiga** (`malaga`, no
  `malaga-cf`). Se extrajo la tabla completa de los 20 equipos del bundle de LaLigaApp
  (nombre, slug, fullName) — útil para el paso 5 y para no tener que redescubrirla.
- **Bug real cazado por el test, no por lectura**: el `href` del jugador a veces trae un
  sufijo de temporada (`.../jugadores/dani-sanchez/laliga-26-27`). El slug es solo el
  primer segmento tras `/jugadores/`, no todo lo que sigue.
- **Pista fuerte para el paso 5**: la página de equipo también expone
  `data-valor-laliga-fantasy` — el valor de mercado del jugador **tal y como LaLiga lo
  publica**, visto desde futbolfantasy.com. Cruzar esto contra `marketValue` de la API
  oficial (mismo jugador, ambos IDs distintos) puede ser una señal fuerte de matching,
  además del nombre/slug. No se usa todavía — se decide en T5.2.
- **Separación deliberada `parsers.py` / `degradacion.py`**: los parsers son puros (HTML
  -> datos) y ya degradan solos ante HTML roto; la capa de red (timeouts, sitio caído,
  5xx) se degrada aparte, en `get_html_seguro` + `degradacion.py`. Así cada capa se
  prueba con el tipo de fallo que le corresponde, sin mezclar mocks de red con fixtures
  de HTML.

### Paso 1 (2026-08-25)

- **Nombre del proyecto resuelto**: el paquete distribuible se llama `fantasy-scout`
  (igual que el repo y el README); el paquete Python sigue siendo `fantasy`. Queda
  cerrada la nota operativa del `design.md` sobre `fantasy-copilot`.
- **El puerto 8000 de la WSL ya está ocupado** por otro servicio del usuario (una API de
  predicción). Los scripts de verificación aceptan `PUERTO=...`; se usó el 8010.
- **Chrome headless impone un ancho mínimo de ventana (~500px)**: `--window-size=375`
  devuelve un *recorte* del render a 500px, no un render a 375px, y eso simula un
  desbordamiento que no existe. Para verificar el layout móvil de verdad hay que embeber
  la página en un `iframe` de 375px (ver `scripts/capturas_maqueta.sh`).
- **Marcado único para ambos layouts**: la tabla se convierte en cards con `display:block`
  + `td::before { content: attr(data-etiqueta) }`. Evita duplicar el HTML y mantiene el
  "sin JS propio" de R4.
- **La invariante del bloque analítico se valida en el propio tipo**
  (`BloqueAnalitico.__post_init__`): o hay datos + origen, o hay motivo de
  indisponibilidad. Así los pasos 7-8 no pueden colar un `unavailable` con datos a medias.
- **`FANTASY_CONTACTO` resuelto** (2026-08-27): `mailto:nicolas.rdguezgomez@gmail.com` en
  `.env` local. No tiene por qué coincidir con el email de login de LaLiga — son sistemas
  distintos; este es solo el contacto que exige la etiqueta de scraping (R20), para que
  futbolfantasy.com pueda avisar si el scraping da problemas.
  User-Agent resultante: `fantasy-scout/0.1 (+mailto:nicolas.rdguezgomez@gmail.com)`.
- **`analiticafantasy.com` no aparece en el código de LaLigaApp**, solo `futbolfantasy.com`.
  No es una confirmación de que analiticafantasy.com no sirva, solo de que esta fuente
  concreta no lo usa — se revisa al llegar al paso 4 si hace falta una segunda fuente para
  "probabilidad de jugar" (futbolfantasy.com ya cubre tendencia de valor y alineaciones).

### Paso 3 — reconocimiento (2026-08-26)

- **El HAR no hizo falta: el reconocimiento se resolvió leyendo el código de un cliente
  open-source ya instalado en local** (`Externoak/LaLigaApp` v3.5.3, en una copia local
  de LaLigaApp). Host, rutas y cabeceras salieron de
  `resources/app/server/config.js` y del bundle `build/static/js/main.*.js`. Documentado en
  `design.md` §Fuente 1. **Cuando esta API cambie, releer ese cliente es más rápido que
  recapturar tráfico** — merece la pena conservar la copia local.
- **Camino que NO funcionó, para no repetirlo:**
  - *DevTools sobre LaLigaApp*: la app monta su **propio proxy local** en `127.0.0.1:3005`,
    así que DevTools solo ve la conversación app↔proxy, sin el host real ni la cabecera
    `Authorization`. El HAR resultante (`data/raw/127.0.0.1.har`) sirve igualmente: tiene
    el **body real de `/market`** (81 KB), útil como fixture.
  - *HTTP Toolkit interceptando el Electron*: rompe la app
    (`ERR_HTTP_HEADERS_SENT` en `ProxyServer.handleProxyReq`) — dos proxies pisándose.
  - *Proxy MITM al iPhone*: la conexión TCP llegó a funcionar (puerto 8001 alcanzable, ping
    OK, sin aislamiento de red), pero la descarga del perfil de certificado en Safari nunca
    se disparó. Se abandonó al encontrar la vía del código local. Si algún día hace falta
    de verdad, el bloqueo estaba en el certificado, no en la red.
- **`x-app: 2` es una cabecera obligatoria no adivinable.** Es el tipo de detalle que solo
  aparece leyendo un cliente que ya funciona; primer sospechoso ante 4xx inexplicables.
- **Inconsistencia real de la API**: mercado usa `/league/{id}` (singular) y plantilla
  `/leagues/{id}` (plural). Hay que respetarlo literalmente.
- **El `leagueId` es una cadena, no un entero**: `0XXXXXXXX` (anonimizado). El cero inicial es
  significativo; parsearlo como número lo rompe.
- **La vigencia real del token se puede resolver con datos propios**: el `access_token` es
  un JWT, así que basta decodificar su campo `exp` para saber la caducidad exacta y cerrar
  la duda de 24h vs. 30 días de `VIGENCIA_POR_DEFECTO`. Pendiente de hacer con el token
  real, antes de tocar `token_store.py`.
- **La app de terceros implementa refresh de token** (`refresh_token` + `isTokenExpired`).
  Nuestro diseño lo descarta a propósito (el usuario repega el token), pero si la caducidad
  real resulta ser de horas, conviene reabrir esa decisión con el usuario.

### Paso 3 — hallazgos con datos reales (2026-08-26)

- **Caducidad del token resuelta con datos propios**: `exp - iat = 86400 s` exactos, o sea
  **24 horas**. Emisor `login.laliga.es` (Azure B2C). Confirma lo que decía el tercero.
  `VIGENCIA_POR_DEFECTO` corregida de 30 días a 24 h.
- **Mejor que asumir 24h: leer el `exp` del propio JWT.** El usuario pega el token cuando
  este ya lleva horas emitido; contar 24h desde el pegado lo daría por válido de más.
  `expiracion_del_jwt()` lo lee, con el respaldo de 24h solo si el token deja de ser un JWT.
- **`/market` mezcla dos poblaciones** distinguidas por `discr` — ver design.md. Solo
  entran los `marketPlayerLeague` (13 el día de la captura, todos venciendo a las **18:00**
  exactas, lo que confirma el diseño del cron). Los 62 `marketPlayerTeam` (ventas de otros
  managers) se descartan por decisión del usuario.
- **`playerMaster` NO tiene la misma forma en mercado y en plantilla**, pese a lo que
  parecía a primera vista. Lo cazó el test, no la lectura:
  - mercado: `teamId` (int) y `position` (texto)
  - plantilla: `team` (objeto, con `id` como **cadena**) y **sin** `position`
  - `positionId` está en ambos ⇒ es la fuente canónica de posición.
    Mapeo deducido de datos reales: `1 Portero, 2 Defensa, 3 Centrocampista, 4 Delantero,
    5 Entrenador`. Una posición nueva lanza error en vez de pasar desapercibida.
- **`playerStatus` (`ok`/`doubtful`/`injured`/`suspended`/`out_of_league`) es dato OFICIAL,
  no la analítica scrapeada.** Es tentador presentarlo como "probabilidad de jugar": no lo
  es, es un estado administrativo. No se mezclan ni uno rellena la ausencia del otro.
- **Las fixtures de test se anonimizan** (`scripts/generar_fixtures.py`): el repo es
  público y las respuestas reales llevan `managerId`, `leagueId` y `sellerTeam` con nombres
  de la liga privada. Se conservan los nombres de futbolistas (son públicos) y la forma de
  los datos, que es lo único que los tests comprueban.
- **El `slug` existe y es limpio** (`juan-francisco-funes-arjona`): principal candidato para
  el matching del paso 5, pero **no se da por bueno** hasta T5.2 con ambos lados delante.

### Paso 2 (2026-08-26)

- **Postgres local en contenedor propio del repo**, no en el `postgres` ya corriente de
  otro proyecto local: `docker-compose.yml` levanta `fantasy_scout_postgres` en el puerto
  **5433** (el 5432 ya lo ocupa `laliga_postgres` de `Proyecto_ML_Pred_Ganador`). DB
  `fantasy_scout`, usuario `fantasy`/`fantasy` — solo válido en local, nunca en Render.
- **`.env` se carga desde `config.py` con `python-dotenv`** (`load_dotenv()`, no
  sobrescribe variables ya presentes en el entorno). Así `uv run alembic upgrade head`
  funciona sin flags extra, tal como exige la verificación literal de R32. En Render no
  hay `.env`, así que la llamada es un no-op — coherente con R46.
- **Sesión de usuario sin estado en servidor**: la cookie lleva el `user_id` firmado con
  `itsdangerous` (`SESSION_SECRET`), no hay tabla de sesiones. Más simple y suficiente
  para un grupo cerrado de amigos.
- **`Secure` de la cookie se activa solo fuera de `FANTASY_ENTORNO=local`**
  (`fantasy.auth.rutas._cookie_segura`): en local es HTTP y `Secure` impediría que el
  navegador la guarde; en Render siempre hay HTTPS. Verificado a mano con `curl -i`.
- **`TokenUsuario` asume una vigencia de 30 días** (`VIGENCIA_POR_DEFECTO` en
  `token_store.py`) porque LaLiga no documenta la caducidad real del bearer. Se ajusta en
  el paso 3 si el HAR revela algo distinto — queda marcado en el propio código, no solo en
  este documento.
- **R13 se deja sin marcar a propósito**: la UI para "token inválido ⇒ pedir repegar" vive
  en el paso 7 (T7.5), porque necesita que `/plantilla` llame de verdad a la API oficial
  para poder distinguir un 401 real. El resto del bloque B (R6-R12) sí quedó cerrado en
  este paso.
