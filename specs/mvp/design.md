# Design: MVP completo de fantasy-scout

## Contexto

Web personal para mí y un grupo cerrado de amigos de una liga de LaLiga Fantasy. Cada
usuario ve la analítica de SU plantilla y la del mercado de la liga: tendencia de valor
de mercado y probabilidad de jugar por jugador.

No es un producto público ni comercial. La prioridad declarada es **MVP funcional por
encima de arquitectura perfecta**.

Este documento cubre el MVP entero (9 pasos ya acordados), no un paso aislado.

## Alcance

**Incluye** (los 9 pasos acordados):

1. Estructura del repo (scaffold) + maqueta navegable con datos mock
2. Auth propia de la app (registro/login) + almacenamiento cifrado del bearer token de
   LaLiga que cada usuario pega manualmente
3. Cliente contra la API oficial de LaLiga Fantasy, a partir de HAR/cURL real aportado
   por el usuario — reconocimiento y guardado de respuestas crudas
4. Scraper de futbolfantasy.com / analiticafantasy.com: tendencia de valor y
   probabilidad de jugar por jugador
5. Diseño y construcción del cruce de IDs entre API oficial y sitios scrapeados
6. Modelo de datos en Postgres (snapshots diarios de mercado)
7. Endpoints FastAPI sirviendo plantilla+analítica y mercado+analítica reales
   (sustituyendo el mock del paso 1)
8. Conexión del frontend htmx/Jinja2 del paso 1 a datos reales
9. Cron/scheduler en Render para el snapshot diario post-cierre de mercado

**Fuera de alcance, explícito:**

- **Puja recomendada** — descartada del MVP por decisión del usuario.
- OAuth propio contra LaLiga: no existe forma garantizada de registrar un client contra
  una API no pública. Se sustituye por token pegado a mano (ver §Modelo de auth).
- **Registro público**: no hay. Desde el 2026-09-27 el alta es **por invitación de un solo
  uso** (ver §Modelo de auth y `specs/registro/`). Tampoco hay recuperación de contraseña
  por email ni verificación de email.
- **Analítica de jugadores fuera del mercado del día**: la vista de mercado cubre
  únicamente las subastas de hoy (decisión cerrada, ver §Alcance de la vista de mercado).
- Cualquier build step de JS (bundler, npm, framework SPA).
- Multi-liga por usuario: el MVP asume una liga por usuario. Si la API devuelve varias,
  se elige una y se deja anotado, no se modela como feature.
- Notificaciones (push/email/Telegram).

## Fuentes de datos — separación no negociable

Las dos fuentes se tratan **siempre como categorías distintas**, se etiquetan siempre en
código, en base de datos y en la UI, y nunca se mezclan sin dejar claro el origen.

### Fuente 1 — API OFICIAL de LaLiga Fantasy

- Host: **`https://fantasy-api.llt-services.com`** — **[VERIFICADO con la cuenta real del
  usuario, 2026-08-26]**: 7/7 endpoints respondieron `200`. Descubierto leyendo
  `server/config.js:69` de `Externoak/LaLigaApp` v3.5.3 y confirmado después con llamadas
  propias (`scripts/recon_oficial.py`).

#### Endpoints reales (extraídos del bundle de `Externoak/LaLigaApp` v3.5.3)

Base de rutas: `{host}/api`, con `competitionId = 1` fijo. Todas admiten `?x-lang=es`.
El proxy de esa app **no reescribe la ruta** (`index.js:241`, sin `pathRewrite`), así que
la ruta observada en local es literalmente la ruta real contra el host oficial.

| Necesidad del MVP | Endpoint |
|---|---|
| Usuario actual | `GET /api/v4/user/me` |
| Ligas del usuario | `GET /api/v1/competition/1/leagues` |
| **Mercado del día** | `GET /api/v1/competition/1/league/{leagueId}/market` |
| **Plantilla de un equipo** | `GET /api/v1/competition/1/leagues/{leagueId}/teams/{teamId}` |
| Alineación actual | `GET /api/v1/competition/1/teams/{teamId}/lineup` |
| Jornada actual | `GET /api/v1/competition/1/week/current` |
| Dinero del equipo | `GET /api/v1/competition/1/teams/{teamId}/money` |
| Detalle de jugador | `GET /api/v1/competition/1/player/{playerId}/league/{leagueId}` |

Ojo a la inconsistencia de la propia API, que hay que respetar tal cual: el mercado usa
`/league/{id}` en **singular** y la plantilla `/leagues/{id}` en **plural**. No es un
error de transcripción.

**Cabeceras obligatorias** (`config.js:72-81`):

```
Authorization: Bearer <token del usuario>
x-app: 2
x-lang: es
content-type: application/json
```

`x-app: 2` no es adivinable y probablemente sea obligatoria — se descubre solo leyendo un
cliente que ya funcione. Si un día la API empieza a devolver 4xx sin motivo aparente,
esta cabecera es el primer sitio donde mirar.

**Liga del usuario (dato real capturado, anonimizado)**: `0XXXXXXXX`, equipo `<team_id>`.

**Trampa real de tipos, verificada**: el mismo identificador de liga aparece con dos tipos
y dos valores distintos según el endpoint:

| Endpoint | Campo | Valor | Tipo |
|---|---|---|---|
| `/leagues` | `id` | `"0XXXXXXXX"` | cadena, **con** cero inicial |
| `/league/{id}/market` | `leagueId` | `XXXXXXXX` | entero, **sin** cero inicial |

La URL se construye con la **cadena** (`0XXXXXXXX`). Normalizar ambos a entero rompería la
ruta; compararlos como cadenas sin normalizar daría "no son la misma liga". Hay que
tratarlos explícitamente, y por eso el cliente los modela como campos distintos.

**Estructura real de los datos (verificada 2026-08-26)**

`GET /league/{id}/market` → lista de subastas (75 el día de la captura):

```
{ discr, id, salePrice, expirationDate, status, leagueType, leagueId, numberOfBids,
  playerMaster: { id, teamId, name, nickname, slug, positionId, position,
                  marketValue, playerStatus, points, averagePoints, images } }
```

`GET /leagues/{id}/teams/{teamId}` → plantilla (13 jugadores en la captura):

```
{ players: [ { buyoutClause, managerId, playerTeamId, buyoutClauseLockedEndTime,
               isShielded, manager, playerMaster: {...igual que arriba...} } ],
  loanedPlayers, teamMoney, playersNumber, teamValue, teamPoints, position, manager }
```

`playerMaster` es la **misma forma** en mercado y en plantilla — un solo parser sirve para
ambos, y `playerMaster.id` es el identificador oficial canónico para el paso 5.

**`playerStatus` es un dato OFICIAL, no analítica scrapeada.** Valores observados: `ok`,
`doubtful`, `suspended`, `injured`, `out_of_league`. Es tentador presentarlo como
"probabilidad de jugar", pero **no lo es**: es un estado administrativo, no una
probabilidad. Va etiquetado como dato oficial y **nunca** se mezcla ni se sustituye por la
probabilidad scrapeada, que es una estimación de un tercero. Que uno esté disponible no
rellena la ausencia del otro.

**`slug`** (p. ej. `juan-francisco-funes-arjona`) es el campo más prometedor para el cruce
de IDs del paso 5, porque futbolfantasy.com también usa slugs. No se da por bueno todavía:
se decide en T5.2 con datos de ambos lados delante.

#### Qué es "el mercado" en los datos reales — filtro obligatorio por `discr`

`/market` mezcla **dos poblaciones distintas** en una sola lista, distinguidas por el campo
`discr` (verificado 2026-08-26, 75 elementos):

| `discr` | Qué es | Nº | Vencimiento | ¿En el MVP? |
|---|---|---|---|---|
| `marketPlayerLeague` | Jugadores **libres** que saca el sistema | 13 | Todos a las **18:00:00** exactas | **Sí** |
| `marketPlayerTeam` | Jugadores que **otros managers** ponen a la venta | 62 | ~24/48h tras publicarlos, a horas dispares | **No** — decisión del usuario |

**El MVP muestra solo `marketPlayerLeague`.** Los jugadores en venta por otros managers se
descartan por decisión explícita del usuario: no le interesan en la UI.

Consecuencias, todas favorables:

- **Confirma el cierre a las 18:00** y con ello el diseño del cron: los 13 jugadores que sí
  nos interesan vencen todos a esa hora exacta. Las horas dispares del resto pertenecen a
  la población que descartamos, así que no invalidan nada.
- El volumen real a scrapear en el paso 4 es de **~13 jugadores por día**, no 75 ni
  cientos. El matching manual de no-emparejados (T5.7) es perfectamente asumible.
- Las dos poblaciones tienen **campos distintos** — `marketPlayerTeam` trae
  `sellerTeam`, `playerTeam`, `numberOfOffers`, `directOffer`; `marketPlayerLeague` trae
  `numberOfBids`. Un parser que asuma una forma única falla con la otra: **filtrar por
  `discr` antes de parsear**, no después.
- **LaLiga desactivó el login web** (confirmado también por terceros, `alxgarci/
  marca-fantasy-api-scraper-updated`): la única vía de login hoy es la app móvil, o un
  flujo OAuth (Azure B2C) que algunas apps de escritorio de terceros dicen reutilizar.
  Esto no cambia el diseño (el modelo de auth ya asumía token pegado a mano, nunca OAuth
  propio) pero sí confirma que no hay atajo de registrar un client propio.
- **Corrección sobre la caducidad del token**: un tercero reporta que el bearer de LaLiga
  **caduca cada 24 horas**, no en semanas. `VIGENCIA_POR_DEFECTO` en
  `src/fantasy/auth/token_store.py` estaba puesta a 30 días por defecto — hay que bajarla
  a ~24h en cuanto el paso 3 lo confirme con un token real, porque con la vigencia mal
  puesta el usuario creería tener sesión válida cuando en realidad el token ya expiró en
  LaLiga. **Pendiente de confirmar con datos propios antes de tocar el código.**
- Provee: plantilla del usuario, liga, mercado.
- Requiere el **bearer token de cada usuario**.
- API no pública y no documentada: **los endpoints pueden cambiar sin aviso**. No se
  asume que algo que funcionó hace meses siga vivo. Todo cliente contra ella valida la
  forma de la respuesta y falla de forma explícita y legible, no silenciosa.
- Vive en `src/fantasy/official/`.

### Fuente 2 — SCRAPING de terceros

- **futbolfantasy.com**, única fuente de analítica.
- **`analiticafantasy.com` evaluado y descartado (2026-08-27)**, no por comodidad sino por
  imposibilidad legítima: su página de alineaciones probables es una app React cuyo HTML
  **no contiene ningún dato de jugador** (verificado: 0 apariciones de nombres conocidos).
  Los datos los carga por JavaScript desde `/api/`, y su `robots.txt` dice
  `Disallow: /api/`. Sortearlo con un navegador headless acabaría pegando igualmente a esa
  ruta, así que **incumpliría la regla no negociable de respetar robots.txt** — además de
  meter una dependencia de navegador que choca con el "sin build step" del proyecto.
- Provee: tendencia de valor de mercado y probabilidad de jugar.
- Frágil, sin SLA, puede romperse en cualquier momento sin aviso.
- Solo **`robots.txt` de `futbolfantasy.com` comprobado**: `Disallow:` vacío, todo
  permitido para nuestro UA (verificado 2026-08-27). Saber que un tercero scrapea estas
  rutas no sustituye esta comprobación propia — es la regla no negociable, no una
  formalidad.

#### URLs y estructura real (verificadas con HTML capturado, 2026-08-27)

- **Mercado / tendencias de valor**: `https://www.futbolfantasy.com/analytics/laliga-fantasy/mercado`
  — una tabla con una fila `<tr class="elemento_jugador" data-id data-nombre
  data-diferencia1 ...>` por jugador. `data-diferencia1` es la variación de valor de las
  últimas 24h en euros — encaja con la cadencia del snapshot diario. Datos en atributos
  `data-*`, no en texto renderizado: más estable ante un rediseño visual.
- **Probabilidad de jugar por equipo**: `https://www.futbolfantasy.com/laliga/equipos/{slug}`
  — cada jugador es `<a class="camiseta" data-probabilidad="NN%" href=".../jugadores/
  {slug-jugador}">`. El propio sitio lo describe como "probabilidad de ser titular la
  próxima jornada" — es el dato exacto que buscábamos, no una aproximación nuestra.
  **Trampa real**: el `href` a veces trae un sufijo de temporada
  (`.../jugadores/dani-sanchez/laliga-26-27`); el slug es solo el primer segmento.
- **Los slugs de equipo de futbolfantasy.com NO coinciden con los de LaLiga**
  (`malaga` vs. `malaga-cf` en la API oficial). Tabla completa de los 20 equipos
  (nombre, slug, fullName) extraída del bundle de `Externoak/LaLigaApp`.
- **Pista para el paso 5**: la página de equipo también expone
  `data-valor-laliga-fantasy` — el valor de mercado del jugador tal y como lo publica
  LaLiga, visto desde futbolfantasy.com. Cruzarlo contra `marketValue` de la API oficial
  del mismo jugador puede ser una señal de matching adicional al nombre/slug. No se usa
  todavía; se decide en T5.2, no aquí.
- **Regla dura de degradación:** un fallo de scraping NUNCA tumba la app. Plantilla y
  mercado (datos oficiales) se siguen sirviendo igual, con la analítica scrapeada
  marcada como **"no disponible"** en la respuesta y en la UI. Implementado con dos
  capas separadas: los parsers degradan solos ante HTML inesperado (ver
  `scrapers/parsers.py`), y la capa de red degrada aparte ante fallo de conexión/HTTP
  (`ClienteScraping.get_html_seguro` + `scrapers/degradacion.py`).
- Vive en `src/fantasy/scrapers/`.

### Reglas de scraping (no negociables)

- Rate limiting razonable: **nunca concurrente**, peticiones espaciadas en segundos.
- User-Agent identificable con el nombre del proyecto (y forma de contacto).
- Caché local en `data/cache/` para no re-scrapear si no hace falta.
- **Comprobar `robots.txt` antes de tocar cualquier ruta nueva.** Esto se verifica por
  ruta, no una vez por dominio.

### Cobertura real de cada métrica (medido, 2026-08-27; corregido, ver más abajo)

Las dos métricas **no tienen la misma cobertura**, y el diseño lo refleja:

| Métrica | Cobertura | Por qué |
|---|---|---|
| Tendencia de valor | Prácticamente todos | Sale de la tabla de mercado, que lista ~669 jugadores |
| Probabilidad de jugar | Solo el once probable | Sale de la página de alineación de cada equipo |

Sobre el mercado real: 11 de 11 jugadores de campo tienen tendencia; **7 de 11 tienen
además probabilidad**. Los 4 restantes no aparecen en el once probable de su equipo ni
tienen el dato en su ficha individual — comprobado uno a uno.

**Por eso cada métrica lleva disponibilidad propia** (decisión del usuario): exigir ambas
descartaba tendencia real de 4 jugadores. La invariante que sí se mantiene es que **un
dato ausente es siempre `None` + motivo visible**, nunca un 0 ni un guion mudo. En la UI
se ve como "sin dato" con el motivo, distinto del badge de analítica caída entera.

**Bug de cobertura corregido (2026-09-02).** La medición del 27/08 (7 de 11) resultó ser
un **bug del scraper**, no una limitación real del sitio. La página de equipo tiene DOS
widgets de alineación: uno antiguo e incompleto (`a.camiseta[data-probabilidad]`, cubre
~20/26 del equipo) y uno completo, el "campo" (diagrama de fútbol), que envuelve a cada
jugador en `div.jugador_{id}` con `span.probabilidad-widget` dentro — cubre la plantilla
**entera** (26/26), titulares y suplentes por igual. El parser usaba el widget viejo.
Corregido en `parsers.parsear_probabilidad_equipo` para usar el widget del "campo".

Descubierto además que `{id}` en `jugador_{id}` es el **mismo id numérico** que usa la
tabla de mercado (`data-id`): el cruce entre ambas páginas pasó a ser por id exacto en
vez de por nombre con tolerancia a defectos (sufijos, similitud, apellido), lo que de
paso simplificó bastante el matching (`_scrapear_probabilidades` en
`analytics/servicio.py`). Verificado con datos reales del usuario: su plantilla pasó de
11/15 a 15/15 con probabilidad, valores exactos comprobados contra lo que él veía en su
propio navegador (Huijsen 80%, O. Rey 70%, R. Terrats 80%, Ryan 95%).

### Métricas mostradas, y por qué no hay predicciones

La app muestra **solo datos observados**. Métricas actuales:

| Métrica | Origen | Qué es |
|---|---|---|
| Valor de mercado | API oficial | Valor actual del jugador |
| Tendencia de valor | futbolfantasy | Variación de las últimas 24 h (`data-diferencia1`) |
| Media 7 días | futbolfantasy | `data-diferencia7` (acumulado de la semana) dividido entre 7 |
| Probabilidad de jugar | futbolfantasy | % del once probable |

**Decisión del usuario (2026-08-28): no se muestra ninguna previsión de valor futuro.**
Se evaluó añadir una proyección a 7 días y se descartó. Ninguna fuente publica ese dato
—futbolfantasy solo tiene históricos (1/2/3/7/14/30 días), y `analiticafantasy` está
descartada por robots.txt— así que habría que **calcularla nosotros**, y sería un dato de
naturaleza distinta a todo lo demás: una estimación entre datos medidos. Se prefirió no
mezclar.

Nota sobre `data-diferencia7`: es el **acumulado** de la semana, no la media. Dividir
entre 7 es lo que la hace comparable con la variación diaria; tratarla como media
multiplicaría por 7 lo que ve el usuario.

Las columnas `Tend.` y `Acel.` de futbolfantasy existen pero **no se usan**: no está
documentado qué calculan y sus valores no están en euros (se observaron `-52`, `1460`).
Mostrar un número sin saber qué mide sería peor que no mostrarlo.

### Rendimiento: la web no scrapea (corregido 2026-08-28)

**Error de diseño detectado al desplegar.** Las pantallas scrapeaban en vivo durante la
petición HTTP. Para pintar el mercado hacían falta **11 peticiones** (la tabla + una por
cada equipo implicado) y el rate limit propio las espacia 5 s: **55 segundos como mínimo**,
más el arranque del servicio dormido. Inservible.

Agravado por el plan free de Render: la caché vivía en el **filesystem**, y el servicio
duerme y reinicia cada 15 minutos, así que casi siempre se scrapeaba desde cero.

**Corrección**: existe `analitica_diaria`, que el cron rellena con **los ~669 jugadores que
publica futbolfantasy**, no solo con los del mercado. La web solo consulta y empareja
(cálculo puro, sin red).

| | Antes | Después |
|---|---|---|
| `/mercado` | 55 s+ | **~1,3 s** |
| `/plantilla` | 55 s+ | **~0,9 s** |

Por qué guardar los 669 y no solo los 13 del mercado: las plantillas contienen jugadores
que **no están en subasta**, así que con un snapshot del mercado `/plantilla` se habría
quedado sin analítica. Cuesta lo mismo — el sitio los da todos en una sola petición.

**Identidad de la tabla**: se guarda por el id de **futbolfantasy**, no por el jugador
oficial. Así el dato queda tal y como llega de la fuente (sin mezclar identidades), y si
el matching mejora, los datos ya guardados se benefician sin migrarlos.

**Antigüedad**: se sirve la captura más reciente de los últimos 3 días y **la fecha se
muestra en pantalla**. Más allá de eso se prefiere "no disponible" a enseñar datos viejos
como si fueran de hoy.

**Idempotencia del cron**: "el día está hecho" exige snapshot **y** analítica. Mirar solo
`market_snapshot` dejaba la web sin datos hasta el día siguiente si la analítica faltaba —
pasó de verdad en local.

### Botón "Actualizar datos": excepción deliberada a "la web no scrapea" (2026-09-02)

Decisión del usuario: el botón "Actualizar datos" de `/plantilla` y `/mercado` sí debe
volver a scrapear futbolfantasy.com en el momento, en vez de solo releer `analitica_diaria`.
Esto reabre a propósito el problema de rendimiento que motivó la sección anterior —
decisión consciente, no un descuido:

- **Se acota el alcance**, no se revierte la arquitectura: `recolectar_analitica(oficiales)`
  (existía ya, sin usar fuera del cron) solo scrapea los **equipos de los jugadores de esa
  pantalla**, no los 669 de la liga. Medido con datos reales: ~7-60 s según cuántos equipos
  distintos toque (peor caso ≈ nº de equipos × 5 s de rate limit), frente a los 55 s+ del
  bug original que tocaba equipos de toda la liga.
- La carga normal de página (`GET /plantilla`, `GET /mercado`) **no cambia**: sigue
  leyendo de BD, instantánea. Solo `/plantilla/tabla` y `/mercado/tabla` (lo que dispara
  el botón vía htmx) piden `en_vivo=True`.
- **La caché de disco de 6h también se salta a propósito** (`ignorar_cache=True`,
  `ClienteScraping.get_html`): sin esto, pulsar el botón dentro de la ventana del cron
  devolvía el mismo HTML de siempre y el botón "no hacía nada" de verdad. El resultado se
  sigue guardando en caché igualmente, para que llamadas posteriores no forzadas sí se
  beneficien.
- El texto "Analítica … capturada el …" (que lee la fecha del cron) se actualiza aparte
  vía `hx-swap-oob` desde el propio fragmento, porque tras un scrape en vivo esa fecha ya
  no describe lo que se está mostrando.
- Moverse entre secciones (`/plantilla`, `/mercado`, `/clausulas`) muestra "Cargando…" vía
  `hx-boost` en la navegación — sin JS propio, coherente con R4/R37.

### Contrato de degradación (cómo se refleja el fallo)

Toda respuesta que combine ambas fuentes lleva el origen explícito por bloque. Forma
concreta a fijar en el paso 7, pero el principio es: los campos oficiales y los campos
analíticos viven en secciones separadas del payload, y la sección analítica puede venir
con `status: "unavailable"` + motivo, sin que eso afecte a la oficial. En la UI eso se
renderiza como un badge "analítica no disponible", nunca como un 0 o un guion mudo que
se confunda con un dato real.

## Modelo de auth

Decisión cerrada:

- **No hay OAuth propio contra LaLiga.** Es una API no pública; no hay garantía de poder
  registrar un client.
- **No hay registro público; el alta es por invitación** (decisión REVISADA el
  2026-09-27, ver `specs/registro/`). El admin crea en la pestaña Invitación un enlace de
  un solo uso que caduca a los 7 días; quien lo abre introduce su email y contraseña de
  LaLiga, se validan contra LaLiga y se crea la cuenta ya conectada. Sin invitación válida
  no hay formulario. Sigue siendo un grupo cerrado de amigos. `scripts/crear_usuario.py`
  queda como puerta trasera.
- **Cuentas de LaLiga de Google u otra red social** (2026-10-12, ver
  `specs/registro-social/`): no tienen contraseña de LaLiga. La misma invitación les ofrece
  el login oficial con código de autorización + PKCE y vuelta a `https://jwt.ms`; se
  mantienen con refresh token y entran en la app con una contraseña propia. Depende de que
  LaLiga mantenga `jwt.ms` registrada; la vigilancia diaria lo comprueba.
- **La contraseña de la app es la de LaLiga** (email normalizado a minúsculas). Hash
  Argon2 en `user` para el login, que no depende de LaLiga; si no casa y LaLiga la acepta,
  se resincroniza (el usuario la cambió allí).
- El token se obtiene **automáticamente** (ver §Login automático, decisión revisada el
  2026-08-28). Pegarlo a mano sigue existiendo como **vía de respaldo** por si LaLiga
  cierra el flujo programático.
- El token se guarda **cifrado en base de datos**, asociado a su usuario, **con
  expiración**.
- El token **nunca se expone al frontend**. No se renderiza en ningún template, no viaja
  en ninguna respuesta, no se loguea. Solo se descifra en memoria en el servidor para
  hacer la llamada saliente.

### Login automático contra LaLiga (decisión REVISADA, 2026-08-28)

**Esto sustituye a dos decisiones anteriores**: "no hay OAuth propio contra LaLiga" y
"cuando el token expira, la UI pide repegarlo; no se intenta refrescar automáticamente".

**Por qué cambia:** el token de LaLiga dura **24 horas**, lo que obliga a repegarlo a
diario. Eso es incómodo para el usuario y, sobre todo, **incompatible con el cron del
paso 9**: un job nocturno no puede depender de que alguien haya pegado un token esa
tarde. Sin esto, el snapshot diario no es realmente automático.

**Qué se descubrió** (leyendo el bundle de `Externoak/LaLigaApp` v3.5.3 y verificado
contra el endpoint real):

```
POST https://login.laliga.es/laligadspprob2c.onmicrosoft.com/oauth2/v2.0/token
     ?p=B2C_1A_ResourceOwnerv2
Content-Type: application/x-www-form-urlencoded

grant_type=password
client_id=af88bcff-1157-40a0-b579-030728aacf0b     (EMAIL_CLIENT_ID)
scope=openid <client_id> offline_access
redirect_uri=authredirect://com.lfp.laligafantasy
username=<email>  password=<password>
response_type=id_token
```

Es el flujo **ROPC** (Resource Owner Password Credentials) de Azure B2C — el mismo que usa
la propia app de LaLiga, no un rodeo inventado. Verificado: con credenciales falsas
responde `AADB2C90225` / HTTP 400, la forma correcta de rechazo.

**Decisión del usuario: opción A, login automático completo.** Se guardan email y
contraseña de LaLiga **cifrados con Fernet** (misma `TOKEN_ENCRYPTION_KEY`), y la app se
autentica sola cuando el token caduca.

**Riesgos aceptados explícitamente por el usuario**, tras exponérselos:

- Si la base de datos se compromete, se compromete **la cuenta de LaLiga entera**, no solo
  un token de 24 h. La contraseña es reversible por diseño: hay que enviarla a LaLiga, así
  que no puede hashearse como la de nuestra app.
- Pedir a los amigos que escriban su contraseña de LaLiga en esta web es una petición
  delicada, sobre todo si alguno reutiliza contraseñas.
- Es una API no documentada: pueden cerrar el ROPC sin aviso. Por eso **se conserva la vía
  manual de pegar el token** como respaldo.

**Mitigaciones aplicadas:**

- Credenciales cifradas, nunca en logs, nunca al frontend, nunca en una respuesta HTTP.
- El login solo se dispara cuando **no hay token válido**: no se reautentica por request.
- Un fallo de credenciales se distingue de un fallo de red, para no reintentar en bucle
  contra el login de LaLiga con credenciales malas.
- Guardar credenciales es **opcional**: quien prefiera pegar el token a mano puede.

Concreción técnica:

- Password hashing con un algoritmo moderno de la librería estándar del ecosistema
  (Argon2 o bcrypt vía `passlib`); no se inventa hashing propio.
- Cifrado del token con clave simétrica leída de `TOKEN_ENCRYPTION_KEY` (Fernet /
  `cryptography`). La clave vive solo en el entorno de Render.
- Sesión por cookie firmada con `SESSION_SECRET`, `HttpOnly` + `Secure` + `SameSite=Lax`.
- Vive en `src/fantasy/auth/`.

## Cruce de IDs — Riesgo de matching

**Este es el punto más frágil del proyecto.**

La API oficial y los sitios scrapeados usan esquemas de identificador de jugador
**distintos y no compatibles entre sí**. No hay clave común garantizada.

Reglas:

- Cualquier cambio en el matching de jugadores **se señala como riesgo explícitamente**.
  Nunca se resuelve en silencio con una heurística improvisada.
- El matching vive aislado en `src/fantasy/matching/`, con su propia representación
  persistida, para que se pueda auditar y corregir sin tocar el resto.
- Existe un fichero de **overrides manuales versionado** en `data/mappings/` — es la
  válvula de escape cuando la heurística falla, y es la razón de que ese directorio SÍ
  se versione mientras `data/raw/` y `data/cache/` están gitignored.
- Todo jugador sin match se trata como **analítica no disponible**, no como dato vacío.
  Un jugador mal emparejado es peor que un jugador sin analítica.
- Se registra el número de no-emparejados en cada ejecución del snapshot; una subida
  brusca es la señal de que algo se rompió upstream.

### Algoritmo de matching (decidido con datos reales, 2026-08-27)

**Lo que NO funciona** (medido, no supuesto, sobre 649 jugadores scrapeados vs. 804
oficiales):

| Estrategia | Aciertos | Por qué se descarta |
|---|---|---|
| Valor de mercado exacto | 17,4% | Los valores se actualizan en momentos distintos; además 86 de 113 coincidencias eran ambiguas (varios jugadores con el mismo valor) |
| Nombre normalizado, sin acotar equipo | 27,4% | futbolfantasy usa nombre completo (`jon karrikaburu`), LaLiga usa apodo (`Karrikaburu`) |

**Lo que sí funciona: acotar por equipo primero, comparar nombres después.**

#### Paso 1 — Puente de equipos (lo que hace viable todo lo demás)

Los identificadores de equipo **no coinciden y no dan error al cruzarlos**: Málaga es
`12` en la API oficial y `11` en futbolfantasy. Ambos son enteros pequeños, así que un
cruce directo por `teamId` emparejaría equipos equivocados **en silencio** — el peor tipo
de fallo posible aquí.

El mapa se construyó por **votación**: se toman los jugadores que casan por nombre exacto
y de forma inequívoca (un solo candidato a cada lado) y se cuenta a qué equipo apunta cada
uno en ambos lados. Resultado: **20/20 equipos, votación unánime, 0 conflictos**.

Vive en `data/mappings/equipos.json`, **versionado**. No se recalcula en cada ejecución:
es estable durante la temporada y un cambio silencioso ahí sería grave.

#### Paso 2 — Comparación de nombres dentro del equipo

Para cada jugador oficial se buscan candidatos **solo en su equipo** (~25 en vez de 649).
Se comparan tres variantes del lado oficial —`name`, `nickname` y `slug` con guiones
convertidos en espacios— contra `data-nombre` del scrapeado, todas normalizadas
(minúsculas, sin acentos, sin puntuación). Se usa `difflib.SequenceMatcher`, de la
librería estándar: sin dependencias nuevas y suficiente para nombres de personas.

Acotar por equipo es lo que resuelve el caso peligroso real observado: `Alfon` tenía dos
candidatos (`alfonso herrero` y `alfonso gonzalez`); dentro de su equipo solo queda el
correcto. **Alfonso Herrero está además en la plantilla del usuario**, así que ese
emparejamiento erróneo habría contaminado datos que el usuario mira a diario.

#### Umbral y decisiones (elegidas por el usuario)

- **Umbral de auto-aceptación: 0,60.** Deliberadamente permisivo. Queda holgado respecto
  al peor caso real entre jugadores de campo (0,91), porque los casos difíciles observados
  —0,67 y 0,80— eran ambos **entrenadores**, que quedan fuera del MVP.
- **Por debajo del umbral: analítica "no disponible"**, y el jugador aparece en el resumen
  de no emparejados para revisión manual. Nunca se rellena con el mejor candidato a
  ciegas.
- **Los entrenadores se excluyen del MVP** (`positionId == 5`). Además de ser los casos de
  matching más flojos, futbolfantasy los identifica con IDs de otra forma (`e119` en vez de
  numérico), lo que confirma que son una población aparte.

**Salvaguarda de ambigüedad (decisión de implementación, no del usuario):** si dos o más
candidatos del mismo equipo superan el umbral y están **a menos de 0,05 de similitud entre
sí**, se trata como *sin match* en vez de elegir el mejor. Con un umbral permisivo como
0,60 esto es lo que evita que dos apellidos parecidos del mismo equipo se emparejen al
azar. Es coherente con la regla dura de arriba: un jugador mal emparejado es peor que un
jugador sin analítica.

#### Resultado sobre el mercado real del 2026-08-27

Excluidos los entrenadores, **11 jugadores**: 9 exactos (1,00) y 2 altos (0,91 `Galdin` ↔
`igor galdin`, transliteración Ihor/Igor; 0,93 `Alfon` ↔ `alfonso gonzalez`). Cero sin
emparejar. Revisados a mano: los 11 correctos.

**Esto es una foto de un día, no una garantía.** Por eso el contador de no emparejados se
registra en cada ejecución (ver arriba): es la señal de que algo cambió upstream.

## Stack (decisiones cerradas)

| Capa | Decisión | Por qué |
|---|---|---|
| Backend | Python + FastAPI + SQLAlchemy 2.0 | Lenguaje del usuario; FastAPI sirve API y HTML en el mismo proceso |
| DB | **Postgres** gestionada por el hosting | Multi-usuario concurrente; SQLite queda descartado por eso |
| Frontend | Jinja2 + htmx + Pico.css | **Sin build step de JS**; experiencia frontend limitada del usuario |
| Diseño UI | Mobile-first: cards apiladas en móvil, tabla en desktop | El uso real es desde el móvil |
| Hosting | **Render**: web service persistente + Postgres + cron job nativo | Vercel descartado: serverless, sin filesystem persistente ni proceso de fondo |
| Dependencias | **uv** | — |

### Identidad visual: la piel de LaLiga Fantasy (2026-09-09)

Decisión del usuario: que la web se parezca a la app oficial de LaLiga Fantasy.

**La paleta no está inventada.** Se extrajo del CSS de producción de
`fantasy.laliga.com` (`main.*.css`), descartando lo que resultó ser Bootstrap 5 por
defecto — que es la mayor parte de los colores del fichero y habría llevado a copiar el
azul `#0d6efd` de Bootstrap creyendo que era de LaLiga:

| Rol | Color | Selector suyo que lo prueba |
|---|---|---|
| Fondo | `#11111f` | `body{background-color:#11111f;color:#fff}` |
| Fondo hundido | `#00000a` | `.main .download-app{background-color:#00000a}` |
| Superficie | `#232433` | `.modal-body{background-color:#232433}` |
| Borde | `#353542` | `header{border-bottom:.5px solid #353542}` |
| Acento | `#ff4b44` | `.primary-text{color:#ff4b44}` |
| Texto tenue | `#7b7b8c` | textos secundarios |

**Dos sitios donde NO se les copia, y por qué:**

- **Su gris tenue `#7b7b8c` da 3,68:1 sobre nuestras cards y no cumple WCAG AA** (4,5:1
  para texto normal). Se aclara a `#9a9aad` (5,54:1) conservando el tono lavanda. Medido,
  no estimado. Es la única desviación de su paleta.
- **Su tipografía (`cs_regular`/`cs_bold`) es propietaria**, así que se aproxima con
  **Barlow** (Google Fonts), del mismo aire deportivo condensado.

**Tema oscuro fijo** (`<html data-theme="dark">`): su app es solo oscura, y mantener
además una paleta clara serían dos juegos de colores que mantener para un usuario que
solo usa el móvil.

Se implementa **sobrescribiendo las variables de Pico** (`--pico-*`) desde
`:root[data-theme="dark"]`, no peleando con sus reglas: así los componentes de Pico
(botones, selects, avisos) heredan la piel sin reescribirlos.

**Más allá del color** (lo que hace que se lea como una app y no como una tabla):

- Ficha de jugador en móvil a **dos columnas**; antes eran cinco filas apiladas por
  jugador, que obligaban a hacer scroll para comparar dos.
- **Posición como insignia** abreviada y con color (POR/DEF/MED/DEL). El texto sigue
  estando: el color solo refuerza, nunca es el único indicador.
- **Probabilidad de jugar con barra y semáforo** (verde ≥75, ámbar 40-74, rojo <40). El
  número nunca desaparece.
- Las **etiquetas de origen** (R39) se vuelven deliberadamente discretas: son requisito y
  siguen visibles, pero aparecen en cada celda y competían con la cifra.
- La sección activa se marca con `aria-current="page"` y subrayado, no solo con color.

## Estructura de carpetas objetivo

```
fantasy-scout/
├── README.md, .gitignore, .env.example, pyproject.toml
├── data/{raw,cache}/         # gitignored
├── data/mappings/            # SÍ se versiona (overrides manuales de ID matching)
├── scripts/                  # one-off, reconocimiento, jobs de cron
└── src/fantasy/
    ├── config.py
    ├── auth/                 # auth propia de la app (usuarios, sesión)
    ├── official/             # cliente de la API oficial de LaLiga
    ├── scrapers/             # scraping de terceros
    ├── matching/             # cruce de IDs
    ├── storage/              # modelos SQLAlchemy, engine, sesión
    ├── analytics/
    └── api/                  # FastAPI app, routers, templates Jinja2
```

Nota operativa: el directorio de trabajo actual es `fantasy-scout`; el proyecto se llama
`fantasy-copilot`. Se resuelve al crear el repo (paso 1) — el nombre del paquete Python
es `fantasy` en cualquier caso, así que no afecta al código.

## Alcance de la vista de mercado (decisión cerrada)

El "mercado" del MVP son **solo las subastas del día**: los jugadores que la API oficial
devuelve como en mercado en ese momento. No se scrapea la liga entera.

Consecuencias que esto fija:

- El cron nocturno scrapea del orden de decenas de jugadores, no cientos. Con el rate
  limit serial de segundos entre peticiones, la ventana de ejecución es de minutos, no
  de horas.
- El matching de IDs (paso 5) solo tiene que resolver los jugadores en subasta ese día,
  lo que hace viable revisar a mano los no-emparejados y meterlos en
  `data/mappings/`.
- Consultar la analítica de un jugador arbitrario que no esté en subasta queda fuera del
  MVP.

## Retención e histórico (decisión cerrada)

**No se garantiza retención larga de `market_snapshot`.** El valor histórico de mercado
es re-obtenible en cualquier momento vía la API oficial o vía las webs scrapeadas, así
que la base de datos no es la fuente de verdad del histórico — es una **caché operativa**
para no re-pedir lo mismo cada vez que se pinta una pantalla.

Consecuencias:

- Perder la tabla de snapshots es un incidente recuperable, no una pérdida de datos.
  Esto quita el backup de Postgres del camino crítico del MVP.
- Se purga lo más antiguo de forma perezosa (retención por defecto **90 días**, ajustable
  por config) para no hacer crecer el Postgres de Render sin motivo. Si más adelante
  quieres tendencia a largo plazo calculada en casa en vez de leída de terceros, se sube
  el número; no hay nada en el diseño que lo impida.
- La **tendencia de valor** que se muestra viene de la fuente scrapeada, no de un cálculo
  propio sobre el histórico. El histórico propio queda como respaldo, no como motor.

## Modelo de datos (esbozo)

Detalle completo en el paso 6, pero las entidades cerradas ya:

- `user` — email, password_hash, timestamps.
- `user_token` — user_id, token cifrado, expiración, timestamps. Nunca sale del backend.
- `player` — identidad canónica del jugador según la **API oficial** (fuente de verdad
  para identidad).
- `player_mapping` — enlace entre `player` y el identificador de cada sitio scrapeado,
  con origen (`heuristica` / `override_manual`) y confianza. **[Forma exacta SIN
  VERIFICAR, paso 5.]**
- `market_snapshot` — **snapshot diario de las subastas del día**: fecha, jugador, valor,
  y campos analíticos scrapeados con su marca de disponibilidad. Es una **caché
  operativa**, no la fuente de verdad del histórico (ver §Retención e histórico).

Los campos concretos de la API oficial y de los datos scrapeados están **verificados**,
ver §Fuente 1 y §Fuente 2 más arriba. Lo único que queda `[SIN VERIFICAR]` en todo el
documento es el algoritmo de matching del paso 5 (ver esa sección).

## Estrategia de deploy (REVISADA 2026-08-28)

**Lo que cambió y por qué**: el diseño original asumía "Render: web service + Postgres +
cron job nativo". Al ir a desplegar se comprobó que **el plan gratuito de Render no
sostiene eso**:

| Límite verificado | Consecuencia |
|---|---|
| Los cron jobs **no existen** en el plan free | El paso 9 no puede correr en Render |
| El Postgres free **caduca a los 30 días** | Se perderían usuarios y credenciales cada mes |
| El web service free duerme tras 15 min | Primera visita del día ~1 min |

Coste de hacerlo todo en Render: ~14 $/mes (7 web + 6 Postgres + 1 cron). **El usuario
optó por la vía gratuita**, repartiendo las tres piezas:

| Pieza | Dónde | Por qué |
|---|---|---|
| Web | Render free | Deploy desde GitHub, HTTPS incluido |
| Postgres | **Neon** | Free permanente, **no caduca y no pausa por inactividad** |
| Cron | **GitHub Actions** | Gratis e ilimitado en repos públicos |

**Por qué Neon y no otro free tier** (comparados: Supabase, Aiven, Prisma Postgres,
Render): el criterio no fue el tamaño —el uso real es <1 MB, todos sobran— sino la
**pausa por inactividad**. Supabase pausa tras una semana sin uso, justo el patrón de este
proyecto; Aiven se reserva reclamar servicios sin uso prolongado. Neon no pausa (solo
escala el cómputo a cero y despierta al conectar) y no caduca.

**Separación de entornos** (aprendido a base de un susto): los tests se ejecutaron una vez
contra Neon y tardaron 51 s en vez de 5, pero lo grave no era la lentitud: la suite **crea
y borra datos**, y ya hubo un test que borraba snapshots reales por usar la fecha de hoy.
Desde entonces:

- `.env` → Postgres local de Docker. Desarrollo y tests.
- `.env.produccion` (gitignored) → Neon. Solo mantenimiento puntual:
  `uv run --env-file .env.produccion alembic upgrade head`.
- Render y GitHub Actions → sus propias variables, nunca en el repo.

### Flujo de trabajo con ramas (acordado 2026-08-28)

**Se trabaja en `feature/v0.1.0`; a `main` solo llega lo probado.** Render despliega
automáticamente desde `main`, así que un commit intermedio en esa rama va directo a
producción.

Secuencia por defecto:

```bash
git checkout feature/v0.1.0     # aquí se commitea
# ... trabajo y verificación ...
git checkout main && git merge feature/v0.1.0 && git push
git checkout feature/v0.1.0     # volver, para no commitear en main sin querer
```

Ya pasó una vez: tras un merge para desplegar, el trabajo siguió en `main` sin avisar. No
tuvo consecuencias (fue fast-forward), pero es exactamente el descuido que este acuerdo
evita. **Al empezar cualquier tanda de commits, comprobar la rama antes.**

### Acuerdos originales que se mantienen

- **Repo en GitHub**, servicio conectado en Render con **auto-deploy sobre la rama
  `main`**, modo **On Commit** para empezar (soy el único que commitea por ahora).
  Reconsiderar un gate de CI si el proyecto crece o entra alguien más a commitear.
- **Variables de entorno** (`DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET`)
  viven **en el dashboard de Render**, nunca en `render.yaml` ni en el repo.
  `.env.example` documenta los nombres, jamás los valores.
- **Migraciones de base de datos nunca van en el mismo deploy que el código que depende
  de ellas.** Primero se despliega la migración (compatible hacia atrás), se verifica, y
  el código que la usa va en un deploy posterior. Esto evita romper producción con datos
  reales de usuarios.
- El job del snapshot (paso 9) ejecuta un script de `scripts/`, del mismo repo. **Ya no
  corre en Render sino en GitHub Actions** (`.github/workflows/snapshot-diario.yml`), por
  lo explicado arriba. El doble disparo idempotente y la guardia horaria siguen siendo
  exactamente los diseñados: solo cambia quién aprieta el botón.
- **Los secretos de producción viven en tres sitios distintos y ninguno es el repo**:
  Render (web), GitHub Secrets (cron) y `.env.produccion` local (mantenimiento). Si algún
  día se rota `TOKEN_ENCRYPTION_KEY` hay que cambiarla en los tres, o las credenciales
  guardadas dejarán de descifrarse.

## Riesgos

| Riesgo | Probabilidad | Cómo se detecta | Mitigación |
|---|---|---|---|
| **Matching de IDs incorrecto** (jugador con analítica de otro) | Alta | Contador de no-emparejados por ejecución; revisión manual | Overrides versionados; sin match ⇒ "no disponible" |
| Cambio/caída de la API oficial no documentada | Media-alta | Validación de forma de respuesta ⇒ error explícito | Guardar respuestas crudas en `data/raw/`; cliente aislado en un módulo |
| Rotura de los selectores de scraping | Alta | Scraper devuelve vacío / falla parseo | Degradación a "no disponible"; app sigue en pie |
| Token de usuario expirado | Segura, recurrente | 401 de LaLiga | UI pide repegar el token; expiración modelada en DB |
| Ser bloqueado por los sitios scrapeados | Media | 403/429 | Rate limit serial + UA identificable + caché + robots.txt |
| Migración rompiendo prod | Baja pero costosa | Deploy falla / errores en runtime | Migración y código en deploys separados |

## Decisiones de arquitectura

- **Un solo proceso FastAPI sirviendo API + HTML.** Alternativa: backend API + frontend
  separado. Descartada: duplicaría despliegue y exigiría build step de JS.
- **Postgres desde el día 1, no SQLite.** Alternativa: SQLite para el MVP y migrar
  después. Descartada: hay multi-usuario concurrente y Render ya da Postgres; migrar
  después es retrabajo garantizado.
- **htmx en vez de SPA.** Alternativa: React/Vue. Descartada explícitamente por la
  ausencia de build step y la experiencia frontend limitada del usuario.
- **Mock primero (paso 1), datos reales después (pasos 7-8).** Permite cerrar la forma de
  la UI sin depender del HAR ni del scraping, que son las partes bloqueadas por captura
  manual de datos.
- **Snapshots diarios en vez de histórico en vivo.** El valor de mercado cambia una vez
  al día tras el cierre; un snapshot post-cierre basta y evita scraping continuo.
- **Respuestas crudas guardadas en `data/raw/`.** Cuando la API cambie, tener el "antes"
  guardado es la única forma barata de diagnosticar qué cambió.

## Cron: dos relojes distintos (REVISADO 2026-09-02)

**Error de diseño detectado por el usuario.** El cron se programó pensando solo en el
cierre de mercado (18:00), pero el proyecto depende de **dos relojes**:

| Reloj | Qué cambia | Cuándo |
|---|---|---|
| Cierre de mercado | Qué jugadores están en subasta | 18:00 |
| Cambio de valor | El valor de mercado de todos los jugadores | **00:00** |

Capturando solo por la tarde (de hecho a las 21:30-23:30, por el retraso de GitHub
Actions), la analítica guardada reflejaba el estado **previo** al cambio de las 00:00. A la
mañana siguiente la web mostraba el valor nuevo —que se pide en vivo— junto a una tendencia
que describía el día anterior. Dos datos hablando de días distintos.

**Corrección**: el job pasa a tener **dos responsabilidades separadas**, con ritmos propios:

| Qué | Cuándo | Por qué |
|---|---|---|
| **Analítica** (`analitica_diaria`) | En **cada** ejecución | Es lo único que consultan las pantallas. Se guarda con upsert, así que refrescarla varias veces solo mejora la frescura |
| **Snapshot de mercado** (`market_snapshot`) | Una vez al día, tras las 18:00 | Es histórico; el mercado no cambia hasta el cierre siguiente |

Cuatro disparos: dos de madrugada (tras el cambio de valor) y dos de tarde (tras el cierre).
Cada par cubre el cambio CET/CEST.

**Dato relevante descubierto**: `market_snapshot` **no lo lee nadie** salvo el propio cron.
Las pantallas consultan `analitica_diaria`. Por eso la frescura que importa es la de la
analítica, y el snapshot puede mantener su ritmo diario sin afectar a la experiencia.

**GitHub Actions retrasa los crons** hasta 2 horas cuando hay carga (observado: programado
a las 17:30 UTC, ejecutado a las 21:34). Por eso el script decide qué hacer mirando la hora
**real** de Madrid, nunca la programada.

## Cron del snapshot diario (decisión original)

El mercado cierra a las **18:00 hora de Madrid**. El snapshot se toma después del cierre.

El problema: el cron de Render se define en **UTC**, y Madrid alterna CET (UTC+1) y CEST
(UTC+2). Una sola expresión cron en UTC se desfasa una hora media temporada — y si se
elige mal el lado, medio año el job **corre antes del cierre** y guarda datos del mercado
todavía abierto, que es el fallo silencioso peor.

Solución adoptada — **doble disparo idempotente**:

- El cron corre a **`30 16 * * *` y `30 17 * * *` UTC**. Eso son las 18:30 y 19:30 en
  verano (CEST) y las 17:30 y 18:30 en invierno (CET). En ambas estaciones, **al menos
  uno de los dos disparos cae después de las 18:00 de Madrid**.
- El job es **idempotente por fecha local de Madrid**: al arrancar comprueba si ya existe
  snapshot para el día de hoy y, si lo hay, no hace nada y termina en éxito.
- El job además **verifica en su propio código** que la hora local de Madrid es ≥ 18:00
  antes de escribir nada; si no lo es, sale sin hacer nada. Esto es lo que impide que el
  disparo temprano de invierno capture el mercado abierto.
- La zona horaria se maneja explícitamente con `zoneinfo` (`Europe/Madrid`); no se usa
  la hora local del contenedor, que es UTC.

Alternativa descartada: un único cron a `30 17 * * *` UTC. Es más simple, pero en verano
tomaría el snapshot a las 19:30, hora y media tarde, y depende de que nadie mueva el
cierre.

## Decisiones cerradas en esta sesión

- **Registro** → alta manual por el propietario mediante script. Sin signup público.
  *(Revisado el 2026-09-27: alta por invitación de un solo uso, ver `specs/registro/`.)*
- **Alcance del mercado** → solo las subastas del día.
- **Retención** → la DB es caché operativa, no fuente de verdad del histórico; purga por
  defecto a 90 días.
- **Cron** → cierre 18:00 `Europe/Madrid`; doble disparo UTC idempotente y con guardia
  horaria en código.

## Decisiones pendientes (preguntar al usuario)

Ninguna abierta a fecha de cierre de este documento. Las incógnitas restantes no son
decisiones sino **datos por capturar**, y están marcadas `[SIN VERIFICAR]` en su sección:
forma real de la API oficial (paso 3), HTML real de los sitios scrapeados (paso 4) y
esquema real de matching de IDs (paso 5).
```
