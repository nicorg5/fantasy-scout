# Tasks: Próximo rival y casa/fuera

Ocho tareas en cuatro bloques. Ninguna se marca hecha sin ejecutar su verificación.
Ver [`requirements.md`](./requirements.md) y [`design.md`](./design.md).

Leyenda: **[DATO]** = necesita algo que solo puedes aportar tú. **[PROD]** = toca
producción. **[TÚ]** = punto de control: me paro y espero tu confirmación.

Reglas de trabajo (memoria del repo): se trabaja en `feature/v0.1.0`; **commits solo al
final**, tras el end-to-end verificado, sin línea `Co-Authored-By` y con cuerpo breve; el
merge a `main` lo decides tú. **No se lanza `snapshot_diario.py`** para verificar nada:
se verifica con los tests, que lo parchean.

```
1. ORIGEN ──► 2. DATOS Y CRON ──────────────► 4. PANTALLAS Y CIERRE
  T1            T2, T3                           T6, T7, T8
         └──► 3. PRESENTACIÓN Y SERVICIO ──┘
                T4, T5
```

Los bloques 2 y 3 dependen solo de T1 y son independientes entre sí. T5 necesita además
T2 (lee la tabla). El bloque 4 lo necesita todo.

---

## Bloque 1 — Origen del dato

- [x] **T1**: Parser `parsear_proximo_partido(html, nombre_equipo)` y tipo
  `PartidoScrapeado` en `scrapers/parsers.py`, con las reglas de D3. La fixture
  `tests/fixtures/scraping_equipo.html` se amplía con la sección «Próximos partidos» y la
  lista de equipos del menú, recortadas de la captura real del Málaga.
  - Cubre: P1, P2, y las dependencias `[SIN VERIFICAR]` del diseño.
  - Ficheros: `scrapers/parsers.py`, `tests/test_scraping_parsers.py`,
    `tests/fixtures/scraping_equipo.html`.
  - Bloqueante: **[DATO][TÚ]** permiso para hasta 2 peticiones GET en vivo a
    `/laliga/equipos/{slug}` (concedido el 2026-10-18; se usó 1).
  - Verificación: tests con
    - Málaga (captura de agosto) → "Real Madrid", fuera, jornada 3;
    - un partido en casa → en casa;
    - un partido de Copa delante del de liga → se salta y se coge el de liga;
    - sin sección, sin partidos de liga, nombre de equipo que no coincide, primer partido
      de liga mal formado → `None` + registro en el log, sin excepción;
    - **todos** los `nombre` de `data/mappings/equipos.json` están en la lista de equipos
      del sitio.

## Bloque 2 — Datos y cron

- [x] **T2**: Modelo `ProximoPartido` en `storage/modelos.py`, migración Alembic y repo:
  `guardar_partidos_del_dia` (upsert), `leer_partidos_mas_recientes` (fecha más reciente
  dentro de `MAX_ANTIGUEDAD_DIAS`) y `purgar_partidos_antiguos`, en
  `storage/analitica_repo.py`.
  - Cubre: P3 (persistencia), D1.
  - Ficheros: `storage/modelos.py`, `migrations/versions/<rev>_proximo_partido.py`,
    `storage/analitica_repo.py`, `tests/test_analitica_repo.py`.
  - Bloqueante: T1 (tipo `PartidoScrapeado`).
  - Verificación: ciclo `upgrade → downgrade → upgrade` en local; tests de upsert
    idempotente (dos guardados el mismo día = una fila por equipo), de lectura con datos de
    hace 4 días (→ vacío) y de purga.

- [x] **T3**: Una descarga por página de equipo y cron. `degradacion.obtener_pagina_equipo`
  devuelve `PaginaEquipo(probabilidades, partido)` y sustituye a
  `obtener_probabilidades_equipo`. `scrapear_todo_para_guardar` devuelve
  `ScrapingCompleto(tendencias, probabilidades, partidos)`. `snapshot_diario.py` guarda los
  partidos y los purga.
  - Cubre: P3, P8 (lado cron), D2.
  - Ficheros: `scrapers/degradacion.py`, `analytics/servicio.py` (solo la parte del cron),
    `scripts/snapshot_diario.py`, `tests/test_snapshot_diario.py`,
    `tests/test_scraping_degradacion.py`.
  - Bloqueante: T1, T2.
  - Verificación:
    - test de `main()` parcheado → la tabla `proximo_partido` tiene la fila del equipo;
    - el número de llamadas a `get_html` es el mismo que antes del cambio (una por equipo);
    - con la página de equipo sin partido, la analítica se guarda igual;
    - los tests existentes que parcheaban la tupla de 2 están adaptados. Suite en verde.

## Bloque 3 — Presentación y servicio

- [x] **T4**: Tipos `ProximoPartido` y `ProximoRival` en `analytics/presentacion.py`, con
  la invariante (exactamente uno de partido o motivo), y `construir_proximo_rival`
  en `matching/analitica.py`, con los motivos de D5 (entrenador primero).
  - Cubre: P5, P8 (lado tipo), D4, D5, decisión de los entrenadores.
  - Ficheros: `analytics/presentacion.py`, `matching/analitica.py`,
    `tests/test_presentacion.py`, `tests/test_matching_analitica.py`.
  - Bloqueante: T1 (tipo `PartidoScrapeado`).
  - Verificación:
    - `ProximoRival()` y `ProximoRival(partido=…, motivo=…)` lanzan `ValueError`;
    - entrenador → motivo de entrenador aunque haya partido de su equipo;
    - equipo sin mapear → su motivo; equipo sin partido → su motivo;
    - un jugador **sin emparejar** con futbolfantasy sí recibe rival.

- [x] **T5**: Servicio. `Analitica.partidos`; `recolectar_analitica_de_bd` los lee
  siempre, también sin tendencias; `recolectar_analitica` (en vivo) los recoge en la
  misma pasada por las páginas de equipo; `componer()` y `clausulas.obtener_clausulas()`
  rellenan `proximo_rival`. `JugadorPresentado` y `ClausulaPresentada` ganan el campo.
  - Cubre: P4 y P6 (datos), P8.
  - Ficheros: `analytics/servicio.py`, `analytics/clausulas.py`,
    `analytics/presentacion.py`, `tests/test_servicio.py`, `tests/test_clausulas.py`.
  - Bloqueante: T2, T3, T4.
  - Verificación: tests de servicio con BD sembrada → el jugador del Málaga trae "Real
    Madrid", fuera; sin filas de analítica pero con partido → rival presente y analítica
    no disponible; en vivo con el scraping parcheado → un rival distinto del guardado.

## Bloque 4 — Pantallas y cierre

- [x] **T6**: Macro `celda_proximo_rival` en `macros.html` y columna "Próximo rival"
  (`data-etiqueta="Próximo rival"`) justo después de "Jugador" en
  `_tabla_jugadores.html` y `_tabla_clausulas.html`. Insignia Casa/Fuera con texto y
  colores con las variables de la paleta (la app solo tiene tema oscuro).
  - Cubre: P4, P5, P7, D6.
  - Ficheros: `api/templates/macros.html`, `api/templates/_tabla_jugadores.html`,
    `api/templates/_tabla_clausulas.html`, hoja de estilos de `api/`,
    `tests/test_rutas.py`.
  - Bloqueante: T5.
  - Verificación: tests de rutas
    - `/plantilla`, `/mercado` y `/clausulas` contienen "vs Real Madrid" y "Fuera" en la
      fila del jugador del Málaga;
    - un jugador sin rival → "sin dato" con el motivo en `title`;
    - el entrenador → "sin dato";
    - `/plantilla/tabla` con "Actualizar datos" y el scraping parcheado → muestra el
      nuevo rival (P6).

- [x] **T7**: End-to-end en local. **[TÚ]**
  - Cubre: P4, P7.
  - Bloqueante: T6.
  - Verificación:
    - `uv run pytest` en verde;
    - `uvicorn` contra el Postgres local con `proximo_partido` sembrado a mano: las tres
      pantallas en el navegador, en ancho y a ancho de móvil (cards con la etiqueta
      "Próximo rival");
    - te enseño capturas y espero tu visto bueno.

- [x] **T8**: Ensayo contra producción y commit. **[PROD][TÚ]**
  - Bloqueante: T7 aprobada.
  - Verificación:
    - `bash scripts/pre_deploy.sh`;
    - `uv run --env-file .env.produccion alembic upgrade head` (**antes del merge**, con
      tu confirmación);
    - `uv run --env-file .env.produccion uvicorn fantasy.api.app:app --port 8020`: las
      tres pantallas cargan; el rival sale "sin dato" hasta que pase el primer cron, sin
      ningún 500;
    - commit en `feature/v0.1.0`. El merge a `main` lo decides tú.

## Descubrimientos

- **T1 (2026-10-18) — Cambio de fuente.** El selector de alineaciones
  (`select.past-alineaciones option[selected]`), base del diseño original, marca la
  jornada del once probable, no el próximo partido: en vivo seguía en la jornada 9 ya
  jugada (Málaga 0-2 Real Sociedad). La misma página trae «Próximos partidos»
  (`section.lista.partidos.proximos`), con la misma estructura en agosto y en octubre. Se
  cambió con el visto bueno del usuario; D3, P1 y el supuesto 2 están actualizados. La
  abreviatura del rival desaparece (esa sección no la trae).
- **T1 — La sección mezcla competiciones.** En vivo, entre la jornada 10 y la 11 había un
  partido de Copa del Rey (`alt="Copa del Rey"`, fase "Ronda 1"), y en «Últimos» un
  amistoso. Por eso se filtra por logo y fase a la vez.
- **T1 — `html.parser` pasa los atributos a minúsculas** (`data-nombreLocal` →
  `data-nombrelocal`). Ya no afecta al lector final, que usa `alt`, pero conviene saberlo
  para el próximo lector que use `data-*` en camelCase.
- **T1 — Fixture.** En vez de una fixture nueva de 2,4 MB, se amplió la recortada
  `scraping_equipo.html` con la sección «Próximos partidos» y la lista de equipos del menú,
  de la captura de agosto. `data/raw/` está en `.gitignore`, así que ningún test puede
  leer de ahí.
- **T1 — Nombres.** Los 20 `nombre` del mapa coinciden exactamente con la lista de equipos
  del sitio: no hacen falta alias.
- **T1 — Peticiones.** Se hizo 1 de las 2 autorizadas (Málaga). El caso "en casa" se
  verificó con la jornada 12 de esa misma página, sin pedir la del rival.
- **T2 — Migración** `4b6c249bcc09_proximo_partido`, encima de `4370e778ca9a`. Solo crea
  la tabla y su índice; probado el ciclo subir, bajar, subir en local. **Falta aplicarla en
  Neon (T8).**
- **T3 — Firma del cron.** `scrapear_todo_para_guardar` devuelve `ScrapingCompleto`; los
  tests de `snapshot_diario` que parcheaban la tupla están adaptados, y todos parchean
  también `guardar_partidos_del_dia` y `purgar_partidos_antiguos`, para que ningún test
  escriba en la tabla con la fecha de hoy.
- **T3 — Si no hay partidos, el cron no escribe nada** y se conserva el último dato bueno
  (dentro de `MAX_ANTIGUEDAD_DIAS`). Las filas purgadas de partidos se cuentan aparte en
  el log de la purga.
- **T3 — Se quitó `matching.equipos.slug_equipo_futbolfantasy`**, que se quedó sin uso. Su
  aviso (usar el id numérico en la URL da 404) se movió a `recolectar_analitica`.
- **T3 — `recolectar_analitica` (en vivo) ya recoge los partidos**, pero los descarta
  hasta T5, que añade `Analitica.partidos`.
- **T5 — `proximo_rival` es obligatorio** en `JugadorPresentado` y `ClausulaPresentada`.
  Con un valor por defecto, una pantalla que olvidara rellenarlo mostraría "sin dato" sin
  que fallara nada. Los helpers de test que construyen filas a mano están adaptados.
- **T5 — "Actualizar datos" con el mercado de futbolfantasy caído:** `recolectar_analitica`
  devuelve `Analitica.vacia()` antes de visitar las páginas de equipo, así que el rival sale
  "sin datos del próximo partido de su equipo". La ruta normal (desde la BD) sí sirve el
  rival aunque no haya tendencias.
- **T6 — La app solo tiene tema oscuro** (`data-theme="dark"` fijo en `base.html`, "la app
  de LaLiga Fantasy es solo oscura"). Se quitó de T6 y T7 la comprobación en tema claro.
- **T6 — Colores de Casa/Fuera neutros:** azul (`--ll-info`) para casa y gris para fuera.
  No se usan el verde y el rojo de las tendencias ni el ámbar de "sin dato", porque jugar
  fuera no es un dato malo. La jornada va en el `title` del rival.
- **T8 (2026-10-18) — Migración aplicada en Neon** (`4b6c249bcc09`) antes del merge. En el
  ensayo contra producción (puerto 8020) las tres pantallas cargan sin errores y el rival
  sale "sin dato" hasta el primer cron, como se esperaba.
- **T8 — Commits.** Cinco de código (lector → tabla → cron → composición → columna),
  cada uno con la suite en verde, más dos de specs: el usuario sacó `specs/` del
  `.gitignore` para versionarlas. Antes de publicarlas se anonimizaron el id de la liga, el
  del equipo y una ruta local en `specs/mvp/`.
