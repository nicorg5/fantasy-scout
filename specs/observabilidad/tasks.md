# Tasks: Observabilidad de uso y salud

> **Estado (2026-09-25): DESPLEGADO y verificado en producción.** Las 14 tareas y los 22
> requisitos hechos. Solo queda **D8** (confirmar el primer ciclo real del cron y de la
> vigilancia programada); tras él, la spec se da por cerrada. Ver §Despliegue al final.

Catorce tareas en cuatro bloques. Ninguna se marca hecha sin ejecutar su verificación.
Ver [`requirements.md`](./requirements.md) y [`design.md`](./design.md).

Leyenda: **[DATO]** = necesita algo que solo puedes aportar tú. **[PROD]** = toca
producción.

> **Cambio de plan (2026-09-24)**: por decisión del usuario, *commits y despliegue solo al
> final, con el end-to-end verificado en local*. T8 pasa a ejecutarse tras T13, junto con
> T14, y `/uso` se construye sobre datos sembrados. T11 (workflow + email) necesita el
> despliegue y va el último.

El orden no es narrativo, es de dependencia real. En particular, **se despliega antes de
construir la pantalla**: un panel hecho sobre una tabla vacía miente sobre lo que vas a
ver. Mientras tú miras otra cosa, la app recoge datos.

```
1. RECOGER  ──►  2. DESPLEGAR  ──►  3. AVISAR   ──►  4. MIRAR
  T1..T7             T8              T9..T11         T12..T14
                  (empieza a        (funciona con   (ya hay datos
                   acumularse)       datos de 1 día)  de varios días)
```

---

## Bloque 1 — Recoger

- [x] **T1**: Modelo `EventoUso` en `storage/modelos.py` + migración Alembic. Columnas
  según O1, con `ON DELETE SET NULL` en `user_id` e índices por `(user_id, creado_en)` y
  `(creado_en)`.
  - Ficheros: `storage/modelos.py`, `migrations/versions/027754aa533f_evento_de_uso.py`.
  - Bloqueante: ninguno.
  - Verificación: **O1** ✅ — 8 columnas, 2 índices y FK con `ON DELETE SET NULL`
    confirmados por inspección del esquema; ciclo `upgrade → downgrade → upgrade` en verde
    (tras el arreglo del enum, ver Descubrimientos). Suite completa: 176 tests en verde.

- [x] **T2**: **Spike de verificación.** Un test que demuestre los dos supuestos
  `[SIN VERIFICAR]` de los que depende todo lo demás: que una `BackgroundTask` adjuntada
  en un `@app.middleware("http")` se ejecuta después de responder, y que ese middleware ve
  el `302` que genera el handler de `NoAutenticado`.
  - Ficheros: `tests/test_observabilidad.py`.
  - Bloqueante: ninguno.
  - Verificación: ✅ **ambos supuestos se sostienen**. El orden real es
    `ruta → middleware devuelve respuesta → tarea`, y el middleware ve el `302` que genera
    el `exception_handler`. D3 y O9 quedan sobre terreno firme.

- [x] **T3**: Módulo `observabilidad/registro.py` con las dos piezas puras: `clasificar(ruta,
  tiene_query) -> (tipo, accion)` y `registrar_evento(...)`, el **único** sitio que
  construye un `EventoUso`. El `try/except` que traga los fallos vive aquí.
  - Ficheros: `observabilidad/__init__.py`, `observabilidad/registro.py`,
    `tests/test_observabilidad.py`.
  - Bloqueante: T1.
  - Verificación: **O4** — tests de `clasificar` para `/plantilla/tabla`, `/clausulas` con
    y sin query, y una ruta desconocida. **O22** — `grep -rn "EventoUso(" src/` devuelve
    exactamente 1 resultado. **O6** — un test con la sesión de BD rota comprueba que
    `registrar_evento` no propaga la excepción.
  - Resultado: ✅ 1 sola construcción de `EventoUso(` (el `grep` crudo casa también con la
    definición de la clase y un comentario: criterio afinado en O22). O4, O6 verificados.

- [x] **T4**: El middleware en `api/app.py`: mide duración, lee el usuario de la cookie con
  `leer_usuario_id_de_cookie`, aplica la lista de exclusión (`/static`, `/health`,
  `/favicon.ico`, `/uso`) y adjunta la escritura a `response.background`.
  - Ficheros: `api/app.py`, `observabilidad/middleware.py`, `tests/test_observabilidad.py`.
  - Bloqueante: **T2 y T3**. No empezar sin el spike en verde.
  - Verificación: **O2** — navegar las cuatro secciones por el menú (`hx-boost`) deja 1
    fila por ruta. **O3** — 10 × `/health` y 3 × `/uso` dejan 0 filas. **O8** —
    `/clausulas` registra `duracion_ms > 1000`. **O9** — `curl` sin cookie deja fila con
    `user_id IS NULL` y `estado_http = 302`. **O7** — comparativa de tiempos con y sin
    registro, diferencia de mediana < 20 ms.
  - Resultado: ✅ O2, O3, O4, O6, O9 verificados con tests sobre la app real; O7 con un test
    estructural y una medición A/B real (ver Descubrimientos). ⚠️ **O8 solo parcial**: se
    comprueba que la duración se mide y se guarda, pero **no** el caso `/clausulas > 1000 ms`,
    que exige un token real de LaLiga. Se comprobará en T8 con datos de producción.

- [x] **T5**: Modelo `EjecucionCron` + migración. Columnas según D7 (`iniciado_en`,
  `terminado_en`, `analitica_guardados`, `snapshot_resultado`, `jugadores_escritos`,
  `con_analitica`, `sin_emparejar`, `error`).
  - Ficheros: `storage/modelos.py`, `migrations/versions/<nueva>.py`.
  - Bloqueante: ninguno (paralelizable con T1–T4).
  - Verificación: ✅ ciclo up → down → up en verde; sin ENUM, no hay tipo huérfano.

- [x] **T6**: `snapshot_diario.py` escribe su fila en un `try/finally`, recogiendo los
  contadores que el script **ya calcula** — incluido `Analitica.sin_emparejar`, el dato
  que R27 del MVP pedía vigilar y que hoy solo se loguea.
  - Ficheros: `scripts/snapshot_diario.py`, `storage/ejecucion_repo.py`,
    `tests/test_snapshot_diario.py`.
  - Bloqueante: T5.
  - Verificación: **O10** — `--forzar-hora` deja una fila con contadores que cuadran con el
    log. **O11** — ejecutarlo antes de las 18:00 sin `--forzar-hora` deja fila con
    `snapshot_resultado = 'mercado_abierto'`. **O13** — test con el escritor roto: el
    snapshot se guarda igual.

- [x] **T7**: `purgar_eventos_uso()` en `storage/retencion.py`, llamada desde el mismo
  punto del cron que las purgas existentes.
  - Ficheros: `storage/retencion.py`, `scripts/snapshot_diario.py`,
    `tests/test_retencion.py`.
  - Bloqueante: T1.
  - Verificación: **O21** ✅ — evento de hace 200 días desaparece, el de ayer sigue (test en
    `tests/test_observabilidad.py`). Suite: 206 en verde.

---

## Bloque 2 — Desplegar (empieza a acumularse)

- [x] **T8** **[PROD]** *(se hace junto con T14, ver §Despliegue)*: Desplegar lo anterior siguiendo el procedimiento del proyecto.
  - Bloqueante: T1–T7 en verde.
  - Verificación, **en este orden exacto**:
    1. `bash scripts/pre_deploy.sh` en verde.
    2. Ensayo: `uv run --env-file .env.produccion uvicorn fantasy.api.app:app --port 8010`
       y navegar las pantallas. *(Ojo: esto ya escribirá eventos reales en producción.)*
    3. `uv run --env-file .env.produccion alembic upgrade head` — **antes del merge**, que
       son dos tablas nuevas.
    4. Merge a `main`, push, y comprobar en Render que el deploy queda verde.
    5. A las 24 h: `SELECT COUNT(*) FROM evento_uso` > 0 y una fila en `ejecucion_cron`.

---

## Bloque 3 — Avisar (la pieza que de verdad te entera)

- [x] **T9**: `observabilidad/consultas.py` con la sesionización por función de ventana
  (umbral como parámetro, según D5) y los agregados de producto.
  - Ficheros: `observabilidad/consultas.py`, `tests/test_observabilidad_consultas.py`.
  - Bloqueante: T1.
  - Verificación: **O5** — 31 min → 2 sesiones; 5 min → 1; umbral a 60 min → los de 31 min
    pasan a ser 1 sola, **sin reescribir datos**. **O12** — serie fecha → cobertura de los
    últimos 14 días.
  - Resultado: ✅ 14 tests, y **5 mutaciones deliberadas del código, todas detectadas**.

- [x] **T10**: `scripts/vigilancia.py`: las tres condiciones de O19, informe legible y
  código de salida ≠ 0 si alguna se cumple. Ventanas de 24/48 h, nunca horas concretas.
  - Ficheros: `scripts/vigilancia.py`, `tests/test_vigilancia.py`.
  - Bloqueante: T6, T9.
  - Verificación: **O19** — con umbrales bajados a propósito contra la BD de producción,
    sale con código ≠ 0 e identifica cuál de las tres saltó. **O20** — la salida identifica
    usuarios por `id`, nunca por email.

- [x] **T11** **[PROD]** **[DATO]** *(fichero escrito y verificado en local; falta la prueba real tras el despliegue)*: Workflow `.github/workflows/vigilancia.yml` a las
  06:00 UTC, con los mismos secrets que el del snapshot. **Y comprobar que el email
  llega de verdad.**
  - Ficheros: `.github/workflows/vigilancia.yml`.
  - Bloqueante: T10 desplegado.
  - Verificación: lanzar con `workflow_dispatch` y umbral forzado a rojo → **te llega el
    email**. Si no llega, revisa tus notificaciones de GitHub: sin ese email, el bloque
    entero no sirve para nada. Después, restaurar umbrales y confirmar verde.

---

## Bloque 4 — Mirar

- [x] **T12**: `FANTASY_ADMIN_EMAIL` en `config.py` y `.env.example`; ruta `/uso`
  protegida, devolviendo aún una pantalla vacía.
  - Ficheros: `config.py`, `.env.example`, `api/app.py`, `render.yaml`,
    `tests/test_uso.py`.
  - Bloqueante: ninguno.
  - Verificación: **O14** — admin → `200`; otro usuario → `404`; sin sesión → `302`;
    variable vacía → `404` para todos (fallo cerrado).

- [x] **T13**: Contenido de `/uso`: los tres bloques de producto, el detalle por usuario y
  los cuatro de salud técnica. Reutiliza `base.html` y `estilos.css`.
  - Ficheros: `api/templates/uso.html`, `api/app.py`, `observabilidad/consultas.py`,
    `tests/test_uso.py`.
  - Bloqueante: T12, T9, y **datos reales de varios días** (T8 desplegado).
  - Verificación: **O15**, **O16**, **O17** — los bloques muestran números que cuadran con
    la misma consulta lanzada a mano en el editor SQL de Neon. **O18** — sobre una BD
    limpia, `200` con "sin datos todavía", nunca un `0` que se lea como "nadie entró".
  - Resultado: ✅ 9 tests del panel + end-to-end real (ver Descubrimientos).

- [x] **T14** **[PROD]** *(junto con T8, ver §Despliegue)*: Desplegar la pantalla. Mismo procedimiento que T8, más
  `FANTASY_ADMIN_EMAIL` cargada en el dashboard de Render **antes** del merge (si no, la
  ruta responderá 404 hasta que la pongas).
  - Bloqueante: T13.
  - Verificación: `/uso` en producción con tu cuenta → `200` con datos reales; con otra
    cuenta → `404`.

---

## Descubrimientos

*(Se rellena durante la ejecución. Aquí van las sorpresas: un supuesto que no se sostuvo,
un campo que no existía, un comportamiento distinto del esperado.)*

### T2 — Los dos supuestos de Starlette se confirman

`response.background` sí funciona adjuntándolo al resultado de `call_next` dentro de un
`@app.middleware("http")`, y el middleware sí ve el `302` que produce el
`exception_handler` de `NoAutenticado`. **D3 y O9 quedan verificados**, ya no son supuestos.

### T2 — `TestClient` espera a las background tasks: O7 no se puede verificar como estaba escrito

El propio spike lo demuestra: `TestClient.get()` no devuelve el control hasta que la tarea
ha terminado. Por tanto **un test con `TestClient` mediría el `INSERT` dentro del tiempo de
respuesta** y "diferencia de mediana < 20 ms" saldría en rojo aunque el diseño sea correcto.

Lo que O7 quiere garantizar es estructural, no temporal: *que la escritura no está en el
camino de la respuesta*. Se reformula en esa dirección (ver O7 en `requirements.md`), más
una medición manual única contra un uvicorn real.

### T1 — Alembic no borra los tipos ENUM en el `downgrade`

`create_table` crea el tipo `tipo_evento` en Postgres, pero `drop_table` **no lo borra**.
Un `downgrade` seguido de `upgrade` falla con
`DuplicateObject: type "tipo_evento" already exists`. Verificado en local y arreglado con un
`op.execute("DROP TYPE tipo_evento")` explícito.

**Las migraciones existentes tienen el mismo agujero** con `origen_mapeo` y
`estado_analitica` (`13339aefec11` y `5d8ba09d24cc`). Nunca ha molestado porque en
producción nadie hace downgrade, pero está ahí. No se toca en esta spec: sería un cambio
sin relación con la observabilidad, y merece su propio commit.

### T4 — El registro bloqueaba el event loop (encontrado midiendo, no con tests)

`registrar_evento` es síncrono (psycopg). Llamado desde una corrutina `async`, el `INSERT`
corría **dentro del event loop** y frenaba a las peticiones siguientes. Los 23 tests
pasaban igualmente: solo lo delató una medición A/B contra un uvicorn real, misma ruta
(`/login`) con el registro activo y desactivado.

| | CON registro | SIN registro | Diferencia |
|---|---|---|---|
| Antes (llamada directa) | ~7 ms | ~2 ms | **+5 ms** |
| Después (`run_in_threadpool`) | ~3,2 ms | ~0,8 ms | **+2,4 ms** |

Arreglado enviándolo al threadpool. El residuo de ~2,4 ms se debe al reparto del GIL entre
el hilo del `INSERT` y el loop, y aparece porque el cliente de medición es secuencial y
pide cada pocos ms; un usuario real no. Está a una décima parte del umbral de O7 (20 ms).
**Lección**: `TestClient` no puede detectar esto, ya que espera a las background tasks.

### T4 — Trampa de shell al medir

`pkill -f "patrón"` casa también con la línea de comandos del propio shell que lo ejecuta
y lo mata (código 144). Para parar servidores de prueba, guardar el PID (`$!`) y usar `kill`.

### T6 — La cobertura solo existe en 1 de cada 4 ejecuciones

`sin_emparejar` y `con_analitica` solo se calculan en la ejecución que guarda el snapshot
(la de tarde). Las demás los dejan a `NULL`. **T9 y T10 deben calcular la cobertura solo
sobre filas `snapshot_resultado = 'guardado'`**: la media de 7 días son 7 puntos, no 28.

### T6 — Los tests de `main()` ensuciaban la BD local

Cada ejecución de `main()` escribe su fila. Neutralizado con un `autouse` y el marcador
`registro_real` (en `pyproject.toml`); los tests del registro usan fecha 2099 para limpiar.

### T6 — No lanzar `snapshot_diario.py` sin preguntar

Se ejecutó para verificar y scrapeó 661 jugadores reales: la BD local tenía un usuario con
credenciales. Sin daño, pero **mirar `laliga_credentials` y preguntar antes**.

### T9 — Dos tests pasaban por casualidad (lo destapó mutar el código)

Quitar el filtro `guardado` de la cobertura no rompía ningún test: el caso de prueba ponía
la ejecución sin contadores *antes* de la de tarde. En el calendario real va **después**
(`ya_existia` a las 17:30 UTC tras guardar a las 16:30): en producción la cobertura habría
desaparecido casi a diario. Igual con las sesiones de dos usuarios intercaladas. Ambos
tests reescritos para reproducir el escenario real.

### T9 — Una sesión de una página tiene duración desconocida, no 0

Mediana y p95 se calculan solo con sesiones de ≥ 2 eventos; las de una página se cuentan
aparte (invariante nº 2).

### T10 — Condición 2 añadida: "ningún snapshot en 36 h"

Sin ella, los disparos de madrugada (que terminan bien) tapaban un fallo del de tarde. La
regla de login pasa a `ultimo_error` (ver O19). Condiciones como funciones puras.

### T13 — End-to-end local (2026-09-24)

App real en `:8013`, dos usuarios temporales (admin y amigo), navegación por HTTP con carga
completa y con cabeceras `hx-boost`. Resultado: 6 filas del amigo, exactas y bien
clasificadas; 0 filas de `/uso`, `/health` y estáticos; `/uso` 200 / 404 / 302 según quién;
vigilancia en rojo con código 1. Usuarios borrados al terminar. Dos detalles corregidos:
el botón salía como `/plantilla/tabla` en vez de "Plantilla", y una sesión de menos de un
segundo se pintaba "0 s" (ahora "menos de 1 s"). **No verificado: el aspecto visual en
navegador y en móvil** (no hay herramienta de navegador en esta sesión).

### Revisión y commits (2026-09-24) — hecho

- [x] Revisión visual de `/uso` en navegador por el usuario (escritorio; móvil con DevTools).
- [x] 6 commits en `feature/v0.1.0`, sin ficheros repartidos, cada estado en verde
      (174 → 182 → 213 → 249 → 249 → 249).

### Revisión del usuario — enlace "Uso" en el menú, solo para el admin

En la revisión visual el usuario echó en falta el enlace. Se añade a `base.html` pintado
**solo para `FANTASY_ADMIN_EMAIL`** (`es_admin`, global de Jinja; id del admin cacheado
por proceso). Es comodidad, no seguridad: la ruta sigue dando 404 a cualquier otro.

### Hallazgo fuera de alcance — el login completo de LaLiga devuelve 404 (en local)

En la BD **local**: `laliga_credentials.ultimo_error` = "el login de LaLiga respondió HTTP
404"; último login correcto el 2026-09-02. Solo falla el **paso 3** (login con contraseña,
política B2C `B2C_1A_ResourceOwnerv2`).

**Producción funciona** (confirmado por el usuario, 2026-09-24). Hipótesis sin verificar:
allí el refresh token (paso 2, otra política) se renueva a diario y nunca llega al paso 3;
el local, 3 semanas sin uso, perdió su refresh token y cayó al 404. Si es así, producción
tiene una fragilidad latente: si la cadena de refresh se corta, no se recupera sola. Tras
desplegar, "Última renovación" en `/uso` lo confirmará o desmentirá. Merece su propia spec.

### Commits — Sublime Merge no veía los commits hechos desde WSL

Sublime Merge (Windows) abre el repo por `\\wsl.localhost\...` y no recibe los avisos de
cambios hechos desde WSL: mostraba una foto vieja. Se arregla reabriendo la pestaña. Además
el Git de Windows veía `pre_deploy.sh` modificado (bit de ejecución invisible por la ruta
UNC) y avisaba de convertir LF→CRLF. Solución, **solo en `.git/config` de este repo**:
`core.filemode=false` y `core.autocrlf=false`. Los dos Git ven ahora el repo limpio.

---

## Despliegue (T8 + T14 + T11)

Todo **[PROD]**: cada paso se confirma con el usuario antes de ejecutarlo.

- [x] D1. `bash scripts/pre_deploy.sh`. El check 4 saldrá en rojo (Neon sin las migraciones
      `027754aa533f` y `1a17fe9d3646`): es lo esperado en este punto.
- [x] D2. Migraciones en Neon: `uv run --env-file .env.produccion alembic upgrade head`.
      Son solo `CREATE TABLE/TYPE/INDEX`, sin tocar tablas existentes: el código viejo de
      Render las ignora. Repetir `pre_deploy.sh` → todo verde.
- [x] D3. Ensayo contra Neon **después** de D2 (sin las tablas, `/uso` daría 500):
      `FANTASY_ADMIN_EMAIL=<tu email> uv run --env-file .env.produccion uvicorn fantasy.api.app:app --port 8010`.
      Escribe eventos reales en producción.
- [x] D4. `FANTASY_ADMIN_EMAIL` en Render → Environment (lo hace el usuario).
- [x] D5. Merge a `main` y push **antes de las 18:30 de Madrid** (horario de verano; 17:30
      en invierno), para que el disparo de tarde del cron ya escriba su fila `guardado`
      con el código nuevo. Si no, la vigilancia de la mañana siguiente dará un falso
      "ningún snapshot en 36 h".
- [x] D6. Verificar en producción: `/health`; navegar; `/uso` 200 con tu cuenta y enlace
      "Uso" visible; "Última renovación" del token (confirma o desmiente la hipótesis del
      refresh, ver §Hallazgo).
- [x] D7 (= T11). Actions → "Vigilancia diaria" → Run workflow con `forzar_alerta` → que
      **llegue el email**. Luego sin forzar → verde (o las alertas reales que haya).
- [ ] D8. A la mañana siguiente: filas en `ejecucion_cron` y vigilancia programada en verde.

Marcha atrás: botón *Rollback* de Render al deploy anterior. Las tablas se pueden quedar
(el código viejo no las usa); no hace falta `alembic downgrade`.

### D3 — Ensayo contra Neon (2026-09-25)

8 filas exactas (4 anónimas del login + 4 del usuario), 0 de `/uso`/estáticos. `/clausulas`
4851 ms → O8 verificado. **Hipótesis del login confirmada**: en producción
`ultimo_login_ok` = 24/09 16:33 UTC (el cron de tarde), sin error; como el login con
contraseña da 404, solo pudo renovarse por refresh. Fragilidad: si el cron deja de correr
unos días, no hay recuperación automática. Nota menor: `creado_en` es el momento del INSERT
(en segundo plano), no el de la petición; peticiones casi simultáneas pueden invertir su
orden por milisegundos. El ensayo se hizo en el puerto 8020: el 8010 lo intercepta una regla
`portproxy` de Windows del equipo del usuario.

### D7 — Email de la vigilancia recibido (2026-09-25)

Ejecución forzada 36131480607 (`gh workflow run ... -f forzar_alerta=true`): falló en 9 s
como estaba previsto y el email "Run failed: Vigilancia diaria" llegó a las 13:49. El email
**no dice qué condición saltó**: hay que abrir la ejecución o `/uso`.

### Posibles retoques detectados en producción (no hechos)

- `HEAD /` sin usuario → 405, seguramente la comprobación de arranque de Render. Aparece en
  la tabla de velocidad.
- Una visita a `/` (solo redirige) cuenta como sección "/" en "Qué miran": darle nombre
  ("Inicio") o ignorarla.
- Escribir cada alerta como anotación `::error::` de Actions, para que el motivo salga en
  el resumen de la ejecución y no haya que abrir el log.
