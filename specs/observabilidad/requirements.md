# Requirements: Observabilidad de uso y salud

Objetivo del feature: **saber quién usa la app, qué mira y cuánto rato**, y enterarse de
que algo se ha degradado sin que lo descubra un amigo por WhatsApp.

Numeración `O#` para no chocar con los `R#` del MVP. Cada requisito tiene verificación
concreta. Ver decisiones en [`design.md`](./design.md) y desglose en [`tasks.md`](./tasks.md).

---

## Supuestos de entrada (corrígeme ahora)

1. **La medición es de servidor, no de cliente.** No se añade ningún `.js` propio ni script
   de terceros: se mantiene la regla de "sin build step". La consecuencia es que se mide
   *lo que se pide al servidor*, no lo que se mira en pantalla (un scroll o un hover no
   existen para esta spec).
2. **Identidad real.** Se guarda `user_id`. Son ~10 cuentas creadas a mano por ti.
3. **Los datos se quedan en Neon.** Ningún tercero, ninguna cookie de analítica de
   terceros y, por tanto, ningún banner de cookies.
4. **El volumen es diminuto**: ~10 usuarios × decenas de navegaciones/día ≈ menos de
   5.000 filas/mes. No hace falta agregación previa ni tabla de rollup.
5. **No hay alta disponibilidad que proteger.** Si el registro de uso falla, se pierde
   telemetría y no pasa nada. Si el registro tumbara una petición, sería un bug grave.
6. **El aviso llega por email de GitHub Actions**, no por Telegram/Slack/PagerDuty.
7. **La monitorización se reparte en tres sitios**, cada uno con su momento: la pantalla
   `/uso` para mirar cuando te apetezca, el **email de GitHub Actions** para enterarte sin
   mirar, y el **editor SQL de Neon** para investigar algo puntual. La pieza que de verdad
   te entera es la segunda, no la pantalla.

---

## Decisiones cerradas (2026-09-23)

| Decisión | Elegido | Motivo |
|---|---|---|
| Dónde vive `/uso` | **Ruta dentro de fantasy-scout** | Reutiliza sesión, estilos y conexión. El caso "la app está caída" lo cubre el email del cron, que corre en otra infraestructura |
| Herramienta | **Postgres propio**, sin terceros | Grafana es una herramienta de operaciones, no de analítica de producto: los embudos y la retención habría que escribirlos a mano en SQL igualmente |
| Puerta abierta al futuro | **Un único punto de emisión** (`registrar_evento()`) | Si la app se abre al público, añadir PostHog o Grafana es editar un fichero, no reinstrumentar |
| Quién puede ver `/uso` | `FANTASY_ADMIN_EMAIL` | Sin migración ni columna nueva; coherente con `config.py` como única fuente del entorno |
| Retención del uso | 90 días (`FANTASY_RETENCION_DIAS`) | Reutiliza `storage/retencion.py` sin variable nueva |
| Avisar a los usuarios del registro | **Fuera de alcance** | Grupo cerrado de confianza. Vuelve a estar sobre la mesa el día que se abra al público |
| Versionado de la spec | Fuera de git, como `specs/mvp/` | Coherencia con lo existente |

---

> **Todos los requisitos verificados** (2026-09-25). O19 en producción: ejecución forzada
> 36131480607 en rojo y email recibido a las 13:49. O8 en el ensayo (`/clausulas` 4851 ms);
> O20 en el log real de Actions (0 emails).

## Bloque A — Registro de uso

- [x] **O1**: Existe la tabla `evento_uso` con, como mínimo: `id`, `user_id` (FK a `user`
  con `ON DELETE SET NULL`, nullable), `ruta`, `tipo`, `accion`, `duracion_ms`,
  `estado_http`, `creado_en` (timestamptz). Ninguna columna guarda token, contraseña,
  email de LaLiga ni la query string cruda.
  *(Revisado por [D5](./design.md#d5): no hay columna `sesion_id`; las sesiones se derivan
  al consultar.)*
  - Verificación: `uv run alembic upgrade head` y luego `\d evento_uso` en psql lista esas
    columnas; revisión visual del modelo confirma que no hay campo de texto libre que
    pueda recibir credenciales.

- [x] **O2**: Cada navegación a una pantalla (`/plantilla`, `/mercado`, `/clausulas`,
  `/token`, `/login`) genera **exactamente una fila**, tanto si la navegación es una carga
  completa como si viene por `hx-boost`.
  - Verificación: con sesión iniciada, visitar las cuatro secciones pinchando en el menú
    (que va por `hx-boost`) → `SELECT ruta, COUNT(*) FROM evento_uso ...` devuelve 1 por
    ruta, no 0 y no 2.

- [x] **O3**: Las peticiones a `/static/*`, `/health`, `/favicon.ico` y **`/uso`** no
  generan filas. `/uso` se excluye porque vive dentro de la propia app: tus visitas al
  panel contaminarían las métricas que el panel muestra.
  - Verificación: `curl localhost:8000/health` 10 veces y visitar `/uso` 3 veces →
    `SELECT COUNT(*) FROM evento_uso WHERE ruta IN ('/health','/uso')` → `0`.

- [x] **O4**: Las acciones explícitas del usuario se registran con `tipo='accion'` y un
  `accion` de un conjunto **cerrado**: `actualizar_datos` (botón de refresco en plantilla
  y mercado) y `filtrar_clausulas`. Un valor fuera del conjunto no se escribe.
  - Verificación: pulsar "Actualizar datos" en `/plantilla` → una fila con
    `tipo='accion'`, `accion='actualizar_datos'`. Aplicar un filtro en `/clausulas` → una
    fila con `accion='filtrar_clausulas'`.

- [x] **O5**: Existe una consulta que **agrupa los eventos de un usuario en sesiones**: dos
  eventos consecutivos separados por más de **30 minutos** pertenecen a sesiones distintas.
  El umbral es un parámetro, no un número incrustado.
  - Verificación: test que inserta eventos separados 31 minutos → la consulta devuelve 2
    sesiones; separados 5 minutos → 1 sesión; y con el umbral cambiado a 60 minutos, los
    separados 31 pasan a ser 1 sola sesión **sin reescribir ningún dato**.
  *(Revisado por [D5](./design.md#d5): derivado en SQL, no guardado. El requisito original
  pedía dos cosas incompatibles entre sí — caducar por inactividad y morir al cerrar el
  navegador — que ninguna cookie puede cumplir a la vez.)*

- [x] **O6**: Un fallo al registrar (BD caída, columna que no existe, lo que sea) **no
  altera la respuesta** que recibe el usuario ni su código de estado.
  - Verificación: test que monkeypatchea el escritor para que lance excepción y comprueba
    que `GET /plantilla` sigue devolviendo `200` con su contenido íntegro.

- [x] **O7**: El registro no añade latencia perceptible: la escritura ocurre **fuera del
  camino de respuesta**.
  - Verificación (estructural): un test comprueba que la escritura viaja en
    `response.background` y **no** se ha ejecutado todavía en el momento en que el
    middleware devuelve la respuesta.
  - Verificación (real, una sola vez y a mano): con `uv run uvicorn` levantado, comparar
    `curl -w '%{time_total}'` sobre `/token` con el registro activado y desactivado. Se
    anota el número en `tasks.md` §Descubrimientos.
  *(Revisado tras T2: `TestClient` **espera** a que terminen las background tasks, así que
  un test de tiempos mediría el `INSERT` dentro de la respuesta y daría rojo aunque el
  diseño sea correcto. Lo que O7 garantiza es estructural, no cronométrico.)*

- [x] **O8**: `duracion_ms` refleja el tiempo que tardó el servidor en generar esa
  respuesta, y `estado_http` su código.
  - Verificación: `/clausulas` (la ruta lenta, ~5 s) registra `duracion_ms` > 1000,
    mientras que `/token` registra un valor de dos o tres cifras.

- [x] **O9**: Una petición **sin sesión válida** (redirección a `/login`) se registra con
  `user_id` nulo, sin inventarse un usuario.
  - Verificación: `curl` sin cookie a `/plantilla` → fila con `user_id IS NULL` y
    `estado_http = 302`.

---

## Bloque B — Salud del cron y del scraping

- [x] **O10**: Existe la tabla `ejecucion_cron` y `snapshot_diario.py` escribe **una fila
  por ejecución**, con: momento de inicio y fin, qué hizo (`snapshot`, `solo_analitica`,
  `omitido_por_hora`, `error`), jugadores capturados, cuántos se emparejaron, cuántos
  quedaron sin analítica y el mensaje de error si lo hubo.
  - Verificación: `uv run python scripts/snapshot_diario.py --forzar-hora` deja una fila
    con contadores coherentes con el log que imprime.

- [x] **O11**: Una ejecución que **no hace nada** por la guardia horaria o por
  idempotencia también deja su fila, con el motivo. No se distingue "no corrió" de "corrió
  y decidió no hacer nada".
  - Verificación: ejecutar el script antes de las 18:00 de Madrid sin `--forzar-hora` →
    fila con resultado `omitido_por_hora`.

- [x] **O12**: La **cobertura de emparejamiento** del día (jugadores con analítica
  emparejada / jugadores totales) es consultable como un número por fecha.
  - Verificación: una consulta SQL sobre `ejecucion_cron` devuelve una serie fecha →
    porcentaje para los últimos 14 días.

- [x] **O13**: Un fallo al registrar la ejecución no impide que el snapshot se guarde.
  - Verificación: test con el escritor roto → el snapshot del día se crea igualmente.

---

## Bloque C — Pantalla `/uso`

- [x] **O14**: `/uso` exige sesión **y** que el usuario sea el administrador. Cualquier
  otro usuario autenticado recibe `404`.
  *(Revisado el 2026-09-27 por `specs/registro` A3: ahora recibe **302 a `/plantilla`**,
  sin ver ningún error.)*
  - Verificación: con la sesión del admin → `200`; con la sesión de otra cuenta → `404`;
    sin sesión → `302` a `/login`.

- [x] **O15**: `/uso` responde tus tres preguntas de producto: **usuarios distintos** que
  entraron (hoy / 7 días / 30 días), **reparto de navegaciones por sección**, y **duración
  de sesión** (mediana y p95).
  - Verificación: tras generar tráfico de prueba con dos cuentas, los tres bloques
    muestran números que cuadran con lo generado.

- [x] **O16**: `/uso` muestra también el detalle **por usuario**: última vez que entró,
  número de sesiones en 30 días y su sección más visitada.
  - Verificación: la tabla lista una fila por usuario con esas tres columnas.

- [x] **O17**: `/uso` muestra la salud técnica: p95 de `duracion_ms` por ruta, número de
  respuestas `5xx` en 7 días, estado del token de LaLiga por usuario
  (`ultimo_login_ok` / `ultimo_error`) y las últimas ejecuciones del cron con su cobertura.
  - Verificación: los cuatro bloques aparecen y el del token coincide con
    `SELECT ultimo_login_ok, ultimo_error FROM laliga_credentials`.

- [x] **O18**: `/uso` no rompe cuando **no hay datos** (base recién migrada): muestra
  "sin datos todavía", nunca un `0` engañoso ni un error 500.
  - Verificación: sobre una BD limpia, `GET /uso` → `200` con los avisos de "sin datos".
    *(Es la invariante nº 2 del proyecto aplicada a esta pantalla.)*

---

## Bloque D — Aviso automático

- [x] **O19**: Existe un workflow diario que falla —y por tanto manda email de GitHub— si
  se cumple alguna de estas condiciones:
  1. **El cron no ha terminado bien ninguna vez en 24 h** (ninguna ejecución, o todas con
     `error`/`sin_usuarios`).
  2. **No se ha guardado ningún snapshot de mercado en 36 h.** Sin esta, los disparos de
     madrugada (que terminan bien) taparían un fallo del de tarde.
  3. **La cobertura de emparejamiento cayó más de 15 puntos** respecto a la media de los 7
     días anteriores con snapshot (mínimo 3 días de referencia; con menos, "datos
     insuficientes", no alarma).
  4. **Algún usuario tiene `ultimo_error`** en `laliga_credentials`: el último intento de
     renovar su token falló y no se ha recuperado.
  - Verificación: tests de cada condición en verde y en rojo; y el script contra la BD
    con los umbrales forzados sale con código ≠ 0 e identifica la condición que saltó.
  *(Revisado en T10: la regla original "más de 48 h sin `ultimo_login_ok`" daba falsas
  alarmas, porque ese campo solo cambia al **renovar** un token, y el cron solo renueva el
  del primer usuario que funciona. Un amigo que no abre la app en dos días no tiene nada
  roto. La condición 2 se añade porque la 1 sola no ve un fallo del snapshot de tarde.)*

- [x] **O20**: El workflow no imprime secretos ni datos personales en su salida: identifica
  a los usuarios por su `id`, no por su email.
  - Verificación: revisar la salida de una ejecución real en la pestaña Actions.

---

## Bloque E — Retención y transparencia

- [x] **O21**: `evento_uso` se purga automáticamente pasados `FANTASY_RETENCION_DIAS`
  (90 por defecto), colgado del job diario existente, como ya se hace con los snapshots.
  - Verificación: test que inserta un evento con fecha de hace 200 días, ejecuta la purga
    y comprueba que desaparece mientras el de ayer sigue ahí.

- [x] **O22**: Existe **un único punto de emisión** de eventos, `registrar_evento(...)`, y
  nadie escribe en `evento_uso` sin pasar por él. Es la costura que permite añadir mañana
  un segundo destino (PostHog, Grafana, un fichero) editando un solo fichero.
  - Verificación: `grep -rn "EventoUso(" src/ | grep -v "class EventoUso"` muestra una
    única **construcción** (`EventoUso(` con argumentos), dentro de
    `observabilidad/registro.py`; el resto son menciones en comentarios. El middleware
    llama a la función, nunca al modelo.
  *(Afinado en T3: el `grep` sin filtrar también casa con la definición de la clase.)*
  - Nota: avisar a los usuarios de que se registra su uso queda **fuera de alcance**
    mientras la app sea privada (decisión de 2026-09-23). Vuelve a ser obligatorio el día
    que haya registro público, junto con política de privacidad y base legal.

---

## Dependencias aún no disponibles

Ninguna externa. Todo se apoya en piezas que ya existen en el repo (Neon, el cron de
GitHub Actions, `storage/retencion.py`, `laliga_credentials`).

---

## Decisiones pendientes

Ninguna. Ver la tabla de **Decisiones cerradas** arriba.

---

## Fuera de alcance (explícito)

- **Session replay, mapas de calor y embudos.** Requieren SDK de cliente; incompatible con
  "sin build step" y desproporcionado para 10 usuarios.
- **Tracing distribuido / OpenTelemetry.** Hay un único proceso; un trace no diría nada
  que no diga ya `duracion_ms`.
- **Métricas de cliente**: scroll, tiempo de foco real en pestaña, Core Web Vitals.
- **Alertas por Telegram, Slack o SMS.** El email de GitHub Actions es suficiente.
- **Panel de terceros** (Grafana, Metabase, PostHog).
- **Medir el cold start de Render** como métrica propia. Se anota como ruido conocido al
  leer las latencias, pero no se instrumenta.
- **Analítica anónima o consentimiento granular.** La app es privada y con cuentas
  nominales; no aplica **mientras siga siendo privada**.
- **Aviso a los usuarios y política de privacidad.** Fuera de alcance hoy por la misma
  razón. Es el primer requisito a reabrir si algún día hay registro público: con usuarios
  desconocidos, guardar `user_id` nominal deja de ser un detalle entre amigos.
- **PostHog, Grafana o cualquier destino de terceros.** No se implementan, pero **O22**
  deja la costura hecha para añadirlos sin reinstrumentar nada.
