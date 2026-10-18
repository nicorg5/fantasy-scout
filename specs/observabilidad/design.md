# Design: Observabilidad de uso y salud

## Contexto

Hoy la app no deja rastro de sí misma. No se sabe quién entra, qué mira ni cuánto rato; y
la salud del punto más frágil del proyecto —el emparejamiento de jugadores entre las dos
fuentes— solo existe como una línea de log que nadie lee y que GitHub Actions borra a los
90 días.

El MVP ya identificó parte de esto. En `analytics/servicio.py`:

```python
# R27: una subida brusca de este numero avisa de que algo se rompio upstream.
sin_emparejar: int = 0
```

El número se calcula, se imprime y se tira. **Esta spec es, en parte, terminar R27.**

## Requisitos que cubre

Todos los de [`requirements.md`](./requirements.md) (O1–O22). Cierra además el agujero
operativo de R27 del MVP.

## Alcance

Cuatro piezas nuevas, ninguna de ellas un servicio nuevo:

```
   navegación del usuario
            │
   ┌────────▼─────────┐
   │   middleware     │  mide duración, lee la cookie, decide si registra
   └────────┬─────────┘
            │  (después de responder — BackgroundTask)
   ┌────────▼─────────┐
   │ registrar_evento │  ◄── ÚNICO punto de emisión (O22)
   └────────┬─────────┘
            ▼
      evento_uso ──────────────┐
                               │
   snapshot_diario.py          │        ┌──────────────┐
            └──► ejecucion_cron┼───────►│  /uso        │  mirar cuando quieras
                               │        └──────────────┘
                               │        ┌──────────────┐
                               └───────►│ vigilancia   │  enterarte sin mirar
                                        │ (GH Actions) │
                                        └──────────────┘
```

Fuera: lo listado en `requirements.md` §Fuera de alcance.

---

## Decisiones de arquitectura

### D1 — La captura vive en un middleware, no en cada ruta

**Alternativas:** un decorador por ruta; una dependencia de FastAPI (`Depends`).

**Por qué middleware:** hay ocho rutas y habrá más. Un decorador exige acordarse en la
novena, y el día que se olvide no falla nada — simplemente esa pantalla deja de existir en
las métricas, en silencio. Es el peor modo de fallo posible en observabilidad.

Además, el middleware es el único sitio que ve **la redirección a `/login`** que produce
`NoAutenticado` (O9) y que puede medir la duración real de la respuesta (O8): una
dependencia se ejecuta *antes* del handler y no sabe cuánto tardó.

### D2 — La identidad se lee de la cookie firmada, sin tocar la base de datos

El middleware llama a `leer_usuario_id_de_cookie()`, que ya existe en `auth/sesiones.py` y
verifica la firma criptográfica. **No hace falta consultar la tabla `user`**: el id sale de
la cookie ya validada, y ahorrar esa consulta mantiene el registro barato.

Consecuencia: un usuario borrado dejaría filas apuntando a un id inexistente. Se resuelve
con `ON DELETE SET NULL` en la FK, no con una comprobación por petición.

### D3 — La escritura ocurre DESPUÉS de enviar la respuesta

**Alternativas:**

| Opción | Coste | Problema |
|---|---|---|
| `INSERT` síncrono | Simple | Suma la latencia de Neon a **cada** respuesta. Viola O7 |
| Cola en memoria + hilo escritor | Rápido | Un hilo más que mantener, y Render free duerme el proceso: se pierden lotes enteros |
| **`BackgroundTask` de Starlette** | Simple | Se pierde el evento si el proceso muere justo después. Asumible |

Elegida la tercera. El middleware adjunta la escritura a `response.background`: Starlette
la ejecuta **una vez enviada la respuesta**, así que el usuario no la espera.

La tarea abre **su propia sesión** desde `obtener_fabrica_sesiones()`: la sesión de la
petición ya está cerrada para entonces (ver `obtener_sesion`, que cierra en el `finally`).

Todo el cuerpo va envuelto en `try/except Exception` que registra en log y traga (O6). Es
la misma regla que `scrapers/` ya aplica: **una pieza accesoria nunca tumba la principal.**

### D4 — Se guarda la PLANTILLA de ruta, nunca la URL cruda

`request.scope["route"].path` devuelve `/plantilla/tabla`, no
`/clausulas?manager=pepe&orden=valor`. Tres ventajas:

1. Las métricas agrupan solas, sin normalizar nada después.
2. **Es imposible que un secreto acabe en la tabla.** Si algún día un token viajara por
   query string, esta tabla no lo vería.
3. No hay explosión de valores distintos.

Si no hay ruta asociada (un 404), se guarda literalmente `"(desconocida)"`.

### D5 — Las sesiones se derivan en SQL, no se guardan ⚠️ **cambia O1 y O5**

Los requisitos asumían una columna `sesion_id` alimentada por una cookie propia. Al
diseñarlo aparece un conflicto: O5 pide que **30 minutos de inactividad** corten la sesión
*y también* que **cerrar el navegador** la corte. Una cookie no puede hacer las dos cosas —
o tiene `max_age` (sobrevive al cierre) o no lo tiene (no caduca por inactividad).

**Propuesta: eliminar la columna y la cookie, y calcular las sesiones al consultar**, con
una función de ventana sobre los eventos de cada usuario:

```sql
LAG(creado_en) OVER (PARTITION BY user_id ORDER BY creado_en)
-- hueco > 30 min  →  empieza sesión nueva
```

| | Cookie `sesion_id` | Derivar en SQL |
|---|---|---|
| Estado que mantener | Cookie + lógica de refresco | Ninguno |
| Cambiar el umbral a 20 min | Solo afecta al futuro | **Recalcula todo el histórico** |
| Usuarios sin sesión | Los agrupa | No los agrupa (aceptable: son rebotes al login) |
| Complejidad | Media, repartida | Una consulta, escrita una vez y con test |

El coste real de la alternativa es que **el umbral queda fijado el día que escribes la
cookie**; derivándolo, es un parámetro que puedes cambiar de opinión sobre él mañana.

**Requiere tu visto bueno**: cambia el texto de O1 (fuera `sesion_id`) y O5 (la
verificación pasa a ser sobre la consulta, no sobre la escritura).

### D6 — `accion` se deduce de la ruta, sin tocar ni una plantilla

No hace falta añadir `hx-vals` ni atributos a los botones. La información ya está en la
URL:

| Petición | Qué significa |
|---|---|
| `GET /plantilla/tabla` | El botón "Actualizar datos" — es su única razón de existir |
| `GET /clausulas` **con** query params | Se aplicó un filtro |
| `GET /clausulas` sin params | Navegación normal |

Se implementa como **función pura** `clasificar(ruta, tiene_query) -> (tipo, accion)`,
testeable sin levantar la app y sin base de datos. El conjunto de acciones es cerrado
(O4): lo que no reconoce, es `navegacion`.

### D7 — `ejecucion_cron` se escribe en un `finally`

El script actual puede terminar de tres formas: bien, `SystemExit(1)` si ningún usuario
pudo leer el mercado, o excepción. Si el registro fuera la última línea de `main()`,
**justo las ejecuciones que fallan —las únicas que importan— no dejarían rastro.**

Por eso: `try/finally` alrededor del cuerpo de `main()`, y la fila se escribe siempre, con
el error si lo hubo.

Columnas, tomadas de lo que el script **ya calcula** (no hay que inventar nada):

| Columna | De dónde sale hoy |
|---|---|
| `analitica_guardados` | `guardados` de `guardar_analitica_del_dia` |
| `snapshot_resultado` | `guardado` / `ya_existia` / `mercado_abierto` / `sin_usuarios` / `error` |
| `jugadores_escritos`, `con_analitica` | ya se loguean en `_guardar_snapshot_de_mercado` |
| `sin_emparejar` | `Analitica.sin_emparejar` — **el dato de R27** |
| `error` | el mensaje de la excepción, truncado |

### D8 — `/uso` se protege con una variable de entorno

`FANTASY_ADMIN_EMAIL` en `config.py`, junto al resto. La ruta depende de `usuario_actual`
(así que sin sesión redirige a `/login`, gratis) y compara el email. Si no coincide,
**404, no 403**: un 403 confirma que la ruta existe. *(Revisado el 2026-09-27 por `specs/registro`, D7: ahora
302 a `/plantilla`, y la dependencia vive en `auth/admin.py`.)*

Si la variable está vacía —como en local o en los tests— la ruta responde 404 a todo el
mundo. Fallo cerrado por defecto.

### D9 — La vigilancia es un script de Python, no SQL dentro del YAML

`scripts/vigilancia.py` reutiliza los modelos y la conexión de la app. Alternativa
considerada: `psql -c "SELECT ..."` dentro del workflow. Se descarta porque ese SQL no se
puede testear, no se refactoriza con el resto del código y duplica el conocimiento del
esquema en un fichero YAML.

El script imprime un informe legible y sale con código ≠ 0 si alguna condición de O19 se
cumple. GitHub manda el email del workflow en rojo — **cero infraestructura de alerta**.

Corre a las 06:00 UTC, suficientemente después del último disparo del cron (01:00 UTC) y
con margen para los retrasos de GitHub Actions.

### D10 — La purga se cuelga del cron existente

`purgar_eventos_uso()` en `storage/retencion.py`, junto a la de snapshots, llamada desde el
mismo sitio. Un cron menos que mantener, exactamente por el mismo motivo que ya está
escrito en ese módulo: *"perezosa a propósito"*.

---

## Dependencias externas involucradas

- [x] **Postgres / Neon** → VERIFICADO: es donde ya vive todo.
- [x] **`leer_usuario_id_de_cookie`, `obtener_fabrica_sesiones`, `purgar_snapshots_antiguos`,
      `Analitica.sin_emparejar`** → VERIFICADO leyendo el código en esta sesión.
- [ ] **`response.background` sobre el resultado de `call_next`** en un
      `@app.middleware("http")` → **[SIN VERIFICAR]**. Es el mecanismo del que depende D3.
      La tarea T2 lo comprueba con un test antes de construir nada encima.
- [ ] **El middleware ve el 302 que produce el handler de `NoAutenticado`** →
      **[SIN VERIFICAR]**. De ello depende O9. Mismo test.
- [ ] **Límite de conexiones del plan gratuito de Neon** → **[SIN VERIFICAR]**. Relevante
      porque cada `BackgroundTask` toma una conexión del pool.
- [ ] **GitHub Actions manda email al fallar un workflow programado** →
      **[SIN VERIFICAR]** en tu configuración concreta de notificaciones. Es el único canal
      de aviso: si no llega, el Bloque D no sirve de nada. T10 lo prueba en real.

---

## Riesgos

| Riesgo | Cómo se detectaría | Mitigación |
|---|---|---|
| **El cold start de Render contamina la latencia.** El plan free duerme el servicio; la primera petición tras dormir tarda decenas de segundos | Un p95 absurdo en una ruta trivial como `/token` | No se instrumenta (fuera de alcance), pero `/uso` muestra **p50 y p95**: si divergen brutalmente, es esto. Se documenta en la propia pantalla |
| **Se pierden eventos al dormirse el proceso** | Huecos en las últimas filas antes de un periodo de inactividad | Aceptado. Es telemetría de producto, no contabilidad |
| **El pool de conexiones se agota** por la sesión extra de cada BackgroundTask | Errores de conexión en el log, app lenta | El engine ya usa pool con `pool_pre_ping`. Verificar el límite de Neon antes de dar por buena la carga |
| **La vigilancia se vuelve ruido y la ignoras** | Emails rojos diarios que archivas sin leer | Tres condiciones, todas de síntoma, ninguna de causa. Si una se dispara dos veces sin acción, **se borra la regla**, no se sube el umbral |
| **`request.scope["route"]` no existe** en 404 o en errores tempranos | `KeyError` en el middleware | Fallback a `"(desconocida)"`, y el `except` general de O6 lo cubriría igualmente |
| **Un retraso del cron de GitHub dispara una falsa alarma** (ya se han visto 2 h) | Email rojo con el cron sano | Las condiciones miran **ventanas de 24/48 h**, nunca "ayer a tal hora" |

## Riesgo específico: datos personales

Esta tabla guarda **qué mira cada persona con nombre y apellidos**. Aunque sean amigos, es
la información más sensible que el proyecto ha generado hasta ahora — más que el propio
snapshot de mercado.

Decisiones tomadas aquí y no al final:

- **Qué se registra**: ruta, acción de un conjunto cerrado, duración, estado. Nunca cuerpo
  de petición, query string cruda, IP ni User-Agent.
- **Dónde vive**: solo en tu Neon. Ningún tercero (decisión de 2026-09-23).
- **Cuánto dura**: 90 días, purgado automáticamente.
- **Quién lo ve**: solo `FANTASY_ADMIN_EMAIL`.
- **Qué pasa si la app se abre al público**: el aviso a los usuarios y la política de
  privacidad, hoy fuera de alcance, pasan a ser **bloqueantes**. Está escrito en
  `requirements.md` §Fuera de alcance para que no se pierda.

---

## Orden de implementación

Dos cadenas independientes que pueden hacerse en cualquier orden, y una pantalla que las
necesita a ambas:

```
A. evento_uso ──► registrar_evento ──► middleware ──┐
                                                    ├──► /uso ──► purga
B. ejecucion_cron ──► escritura en el script ───────┘
                            └──► vigilancia + workflow   (independiente de /uso)
```

Puntos de verificación obligatorios:

1. **Antes de T3 (middleware)**: T2 debe haber demostrado con un test que `BackgroundTask`
   funciona ahí y que el 302 de login se ve. Los dos `[SIN VERIFICAR]` que sostienen D3.
2. **Antes de `/uso`**: tiene que haber datos reales de al menos un día de uso propio. Una
   pantalla construida sobre una tabla vacía miente sobre lo que se va a ver.
3. **Antes de mergear a `main`**: ensayo con `.env.produccion` y **migraciones aplicadas
   antes del merge** — la regla del proyecto, y aquí hay dos tablas nuevas.

## Decisiones pendientes (preguntar al usuario)

1. **D5**: ¿se aprueba eliminar `sesion_id` y derivar las sesiones en SQL? Cambia O1 y O5.
