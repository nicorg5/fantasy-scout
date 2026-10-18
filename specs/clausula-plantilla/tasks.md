# Tasks: Protección de cláusula en Mi plantilla

Cinco tareas en tres bloques. Ninguna se marca hecha sin ejecutar su verificación.
Ver [`requirements.md`](./requirements.md) y [`design.md`](./design.md).

Leyenda: **[DATO]** = necesita algo que solo puedes aportar tú. **[PROD]** = toca
producción. **[TÚ]** = punto de control: me paro y espero tu confirmación.

Reglas de trabajo (memoria del repo): se trabaja en `feature/v0.1.0`; **commits solo al
final**, tras el end-to-end verificado, sin línea `Co-Authored-By` y con cuerpo breve; el
merge a `main` lo decides tú. **No se lanza `snapshot_diario.py`** para nada: esta
feature no toca el cron. No hay migraciones.

```
1. DATO Y TIPO ─────────► 2. SERVICIO Y PANTALLA ─────► 3. CIERRE
  T1 (official)             T3 ──► T4                     T5
  T2 (presentación)
```

T1 y T2 son independientes entre sí. T3 necesita las dos; T4 necesita T3.

---

## Bloque 1 — Dato y tipo

- [x] **T1**: Parseo de la fecha de bloqueo en la plantilla propia (D1).
  - Helper privado `_fecha_de_bloqueo(crudo, camino)` en `official/modelos.py`: `None` si
    falta o viene vacío; `RespuestaInesperada` si no se puede interpretar **o no trae zona
    horaria**. `parsear_plantilla_de_manager` pasa a usarlo.
  - `JugadorPlantilla` gana `bloqueada_hasta: datetime | None`, rellenado con el helper.
  - Cubre: C1, C9 (parte del parseo).
  - Ficheros: `src/fantasy/official/modelos.py`, `tests/test_official.py`.
  - Verificación: `uv run pytest tests/test_official.py tests/test_clausulas.py -q` con
    tests nuevos de:
    - la fixture `plantilla.json` → A. Herrero con `2026-08-21T18:01:01+02:00`;
    - un jugador sin el campo → `None`;
    - una fecha rota y una fecha sin zona → `RespuestaInesperada` con el camino
      `...players[i].buyoutClauseLockedEndTime`, tanto en `Plantilla.desde_api` como en
      `parsear_plantilla_de_manager`.

- [x] **T2**: `formatear_espera` y `ProteccionClausula` en `presentacion.py` (D2, D3).
  - `formatear_espera(segundos)` a nivel de módulo; `ClausulaPresentada.espera_legible`
    la usa sin cambiar su salida.
  - `ProteccionClausula` con `estado`, `segundos_restantes`, `__post_init__`,
    `desde_bloqueo(bloqueada_hasta, blindado, ahora)`, `legible`, `aviso` y la constante
    `UMBRAL_AVISO_SEGUNDOS`.
  - Cubre: C3, C4 (reglas), C5.
  - Ficheros: `src/fantasy/analytics/presentacion.py`, `tests/test_presentacion.py`.
  - Verificación: `uv run pytest tests/test_presentacion.py tests/test_clausulas.py -q`
    con tests nuevos de:
    - `formatear_espera`: 2d 5h, 3h 20m, 15m;
    - la tabla de D2 completa: blindado con fecha y sin ella, sin fecha, vencido,
      exactamente `ahora` (→ clausulable), 23 h (→ `pronto`), 25 h (→ sin aviso);
    - las construcciones incoherentes lanzan `ValueError`;
    - `legible` → "protegido · 2d 5h", "clausulable", "blindado".
    - `tests/test_clausulas.py` en verde **sin modificarlo**.

## Bloque 2 — Servicio y pantalla

- [x] **T3**: El servicio construye la protección (D4).
  - `JugadorPresentado.proteccion_clausula: ProteccionClausula | None`, obligatorio y con
    un docstring de qué significa `None` (pantalla sin columna, no "sin dato").
  - `componer(..., protecciones=None)`; `obtener_plantilla(..., ahora=None)` lo construye
    desde `plantilla.jugadores`; `obtener_mercado` no lo pasa.
  - Se adaptan los helpers de test que construyen `JugadorPresentado` a mano.
  - Cubre: C2 (datos), C6 (datos), C7 (datos).
  - Bloqueante: T1 y T2.
  - Ficheros: `src/fantasy/analytics/presentacion.py`, `src/fantasy/analytics/servicio.py`,
    `tests/test_servicio.py`, `tests/test_rutas.py`, `tests/test_snapshot_diario.py`.
  - Verificación: `uv run pytest tests/test_servicio.py -q` con tests nuevos de:
    - `obtener_plantilla` con `ClienteOficial` parcheado devolviendo la fixture y `ahora`
      fijado → cada jugador con su estado esperado, **ninguno con `None`**;
    - `obtener_mercado` → todos con `None`.
    - Después, `uv run pytest` entero en verde.

- [x] **T4**: Columna "Cláusula" en `/plantilla` y CSS (D5).
  - Macro `celda_clausula(proteccion)` en `macros.html`; cabecera y celda condicionadas a
    `con_clausula` en `_tabla_jugadores.html`; `con_clausula` en el contexto de
    `_pagina_de_jugadores` y `_fragmento_tabla` (`app.py`); estilos `clausula--clausulable`
    y `clausula--pronto` en `estilos.css`.
  - Cubre: C2, C4, C6, C7, C8, C9 (en ruta).
  - Bloqueante: T3.
  - Ficheros: `src/fantasy/api/templates/macros.html`,
    `src/fantasy/api/templates/_tabla_jugadores.html`, `src/fantasy/api/app.py`,
    `src/fantasy/api/static/estilos.css`, `tests/test_rutas.py`.
  - Verificación: `uv run pytest tests/test_rutas.py -q` con tests nuevos de:
    - `/plantilla` con jugadores a 25 h, 23 h, vencido y blindado → los textos
      "protegido · 1d 1h", "protegido · 23h 0m" + "menos de 24 h", "clausulable" y
      "blindado"; clases solo en los dos que tocan; `data-etiqueta="Cláusula"`;
    - `/plantilla/tabla` → también trae la columna;
    - `/mercado` y `/mercado/tabla` → sin la cabecera "Cláusula";
    - `/plantilla` y `/plantilla/tabla` con `obtener_plantilla` lanzando
      `RespuestaInesperada` → 200 con el aviso de LaLiga.

## Bloque 3 — Cierre

- [x] **T5** **[TÚ][PROD]**: Verificación de punta a punta y commits.
  - `uv run pytest` entero en verde y `bash scripts/pre_deploy.sh`.
  - App en local (`uv run uvicorn fantasy.api.app:app --reload`) → revisión visual de
    `/plantilla` en escritorio y a ancho de móvil, y del botón "Actualizar datos".
  - Ensayo contra producción **sin desplegar**, en el puerto **8020** (el 8010 lo intercepta
    el portproxy de Windows):
    `uv run --env-file .env.produccion uvicorn fantasy.api.app:app --port 8020`. Solo
    lectura: hace las mismas peticiones a LaLiga que `/plantilla` hoy. Comparar los
    tiempos con lo que muestra la app oficial y anotar en `## Descubrimientos` si aparece
    un blindado, un jugador sin fecha o una fecha sin zona.
  - Comprobar que la spec no lleva ids reales de liga o equipo ni rutas locales (repo
    público).
  - Commits en `feature/v0.1.0`, al final y agrupados por capa. El merge a `main` lo
    decides tú.
  - Cubre: C8 (comprobación manual) y las dependencias `[SIN VERIFICAR]` del diseño.
  - Bloqueante: T1–T4.

## Descubrimientos

- **T1 (2026-10-18) — La fecha rota ya fallaba bien en Clausulazos**; lo nuevo para los
  rivales es solo el rechazo de la fecha sin zona. El parseo vive en
  `_fecha_de_bloqueo` y lo usan `JugadorPlantilla` y `parsear_plantilla_de_manager`.
- **T1 — `JugadorPlantilla` se construía a mano** en `tests/test_servicio.py`; se adaptó
  con `bloqueada_hasta=None`.
- **T3 — Helpers adaptados** a `proteccion_clausula` obligatorio: dos en
  `test_presentacion.py`, uno en `test_snapshot_diario.py` y `_jugador` de
  `test_rutas.py`, que gana el parámetro `proteccion`. `componer` lo recibe como
  argumento opcional, así que sus llamadas en los tests no cambian.
- **T4 — C6 y C9 ya se cumplían antes de tocar la pantalla**: el mercado nunca tuvo la
  columna y `RespuestaInesperada` ya se traducía en el aviso. Sus tests se quedan como
  regresión, no como RED.
- **T4 — La columna depende de `con_clausula`**, que `app.py` pone con el mismo criterio
  que la variación (`plantilla.html` en la página, `con_variacion` en el fragmento).
- **Suite**: 469 → 502 tests, todo en verde.
- **T5 (2026-10-18) — Ensayo contra producción en el 8020 validado por el usuario**:
  tiempos, resaltado, móvil, "Actualizar datos" y `/mercado` sin columna, todo correcto.
  Sin rarezas en los datos reales.
