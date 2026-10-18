# Design: Próximo rival y casa/fuera

## Contexto

Cada fila de jugador tiene que decir contra quién juega su equipo en la próxima jornada y
si es en casa o fuera (ver [`requirements.md`](./requirements.md)). El dato sale de la
página de equipo de futbolfantasy, que el cron ya descarga cada noche para la probabilidad
de jugar, así que el trabajo consiste en extraer un dato más de un HTML que ya tenemos,
guardarlo y pintarlo.

## Requisitos que cubre

P1–P8. Las decisiones de abajo hacen referencia a cada uno.

## Alcance

Incluye: parser del partido, almacenamiento diario por equipo, lectura en las tres
pantallas, refresco con "Actualizar datos" y la celda nueva en las tablas.

Queda fuera lo que lista `requirements.md`: dificultad, fecha, escudos, calendario, la API
oficial, refrescar por la tarde, ordenar o filtrar por rival.

## El dato en origen (verificado en vivo)

Verificado el 2026-10-18 sobre la página en vivo del Málaga y sobre la captura del
2026-08-27: la estructura es la misma en las dos. La página de equipo trae una sección
«Próximos partidos» con los cinco siguientes del equipo, de todas las competiciones:

```html
<section class="mod lista partidos proximos block-new">
  <a class="ptheight partido nodeco" data-tooltip="Alavés - Málaga" href=".../partidos/22516-alaves-malaga">
    <div class="logo rslogo rsinfo"><img alt="LaLiga" ...></div>
    <div class="equipo local"><img alt="Alavés" ...></div>
    <div class="info rsinfo"><div class="fase"> Jornada 10 </div><div class="date"> Vie 23/10 21:00h </div></div>
    <div class="equipo visitante"><img alt="Málaga" ...></div>
  </a>
  <a ...> <!-- Copa del Rey: logo alt="Copa del Rey", fase "Ronda 1" --> </a>
  ...
```

**Por qué no el selector de alineaciones**, que fue la primera idea. El
`<select class="past-alineaciones">` marca como `selected` la jornada del once probable.
El 2026-10-18 seguía señalando la jornada 9, ya jugada (0-2), mientras «Próximos
partidos» ya daba la jornada 10. Con el selector, la columna se habría quedado en "sin
dato" desde el final de cada jornada hasta que futbolfantasy publica el once siguiente.

## Dependencias externas involucradas

- [x] Sección «Próximos partidos» en la página de equipo → **VERIFICADO** en vivo el
  2026-10-18 y en la captura del 2026-08-27.
- [x] Caso "en casa" → **VERIFICADO** en vivo (Málaga - Racing, jornada 12).
- [ ] Final de temporada → **[SIN VERIFICAR]**: suponemos que la sección se queda sin
  partidos de LaLiga, lo que da "sin dato" (P5).
- [ ] Un partido que se está jugando, ¿sigue en «Próximos» o pasa a «Últimos»? →
  **[SIN VERIFICAR]**. Como el cron corre de madrugada, es improbable que capture un
  partido en juego.
- [x] `data/mappings/equipos.json` da, por id oficial, `id` y `nombre` de futbolfantasy y
  `slug` → VERIFICADO (lo usa ya `matching/equipos.py`).

Las dos primeras se resolvieron en la tarea 1 con **una** petición en vivo (permiso del
usuario, por `ClienteScraping` y su caché).

## Decisiones de arquitectura

### D1 — El rival es un dato del equipo y se guarda por equipo (P3)

Tabla nueva `proximo_partido`, una fila por **equipo de futbolfantasy y día**:

| Columna | Tipo | Nota |
|---|---|---|
| `id` | UUID | PK |
| `fecha` | date | fecha local de Madrid, como `analitica_diaria` |
| `equipo_externo` | str(16) | id numérico de futbolfantasy del equipo ("11") |
| `rival_nombre` | str(64) | "Real Madrid" |
| `en_casa` | bool | |
| `jornada` | int | |
| `origen` | str(64) | "futbolfantasy.com" |
| `capturado_en` | timestamptz | |

`UniqueConstraint(fecha, equipo_externo)` y upsert, igual que la analítica, para que el
doble disparo del cron no duplique filas.

- *Alternativa: columnas nuevas en `analitica_diaria`.* Descartada porque repite el mismo
  dato en ~25 filas por equipo y lo ata al emparejamiento de jugadores: un jugador sin
  emparejar se quedaría sin rival aunque sepamos de sobra de qué equipo es.
- *Por qué esta:* el rival solo depende del equipo, y el equipo del jugador oficial ya se
  cruza con futbolfantasy por `data/mappings/equipos.json`, un mapa exacto y versionado.
  **El rival no pasa por el matching de jugadores**, que es el punto frágil (invariante 3).
  Así un jugador sin emparejar también tiene rival. (Los entrenadores se excluyen a
  propósito en D5, no por falta de dato.)

Migración Alembic nueva. **Se aplica en Neon antes del merge** (CLAUDE.md).
La purga de retención del cron borra también esta tabla, con los mismos
`retencion_dias` que la analítica.

### D2 — Una sola descarga por página de equipo (P3, P6)

Hoy `degradacion.obtener_probabilidades_equipo(cliente, slug)` descarga y parsea la
probabilidad. Se sustituye por `obtener_pagina_equipo(cliente, slug, ignorar_cache)` que
descarga **una vez** y devuelve `PaginaEquipo(probabilidades, partido)`.

- *Alternativa: una función por dato, cada una con su `get_html`.* En el cron la segunda
  llamada saldría de la caché, pero con "Actualizar datos" (`ignorar_cache=True`) haría
  dos peticiones por equipo. Eso rompe P3 y las reglas del scraping.
- Los dos parsers siguen separados y son puros: `parsear_probabilidad_equipo(html)` (sin
  cambios) y `parsear_proximo_partido(html, nombre_equipo)` (nuevo).

### D3 — Reglas del parser `parsear_proximo_partido` (P1, P2)

Devuelve un `PartidoScrapeado | None` y **nunca lanza**. Cuando no puede, devuelve `None`
y deja un `logger.warning` con el motivo:

1. Recorre `section.lista.partidos.proximos a.partido` en orden. Si no hay sección o no
   tiene partidos → `None`.
2. **Se queda con el primero de LaLiga**: logo con `alt="LaLiga"` y fase `Jornada N`. Copa
   y amistosos se saltan (supuesto 6). Las dos condiciones a la vez, para que un cambio de
   rotulación no cuele un partido de Copa como si fuera de liga.
3. **Casa o fuera se decide comparando el nombre del equipo** (el `nombre` del mapa, que
   también viene de futbolfantasy) con el `alt` del escudo local y del visitante. Si
   coincide con el local → `en_casa=True` y el rival es el visitante; si coincide con el
   visitante, al revés. **Si no coincide con ninguno → `None`**: ante la duda no se
   empareja (invariante 3). Un partido de liga mal formado no se salta para coger el
   siguiente: se devuelve `None`, porque saltarlo enseñaría un rival que no es el próximo.

- *Alternativa: el selector de alineaciones* (`option[selected]`, con atributos `data-*`
  más estructurados). Descartada el 2026-10-18 por lo explicado en §El dato en origen: se
  queda atrás entre jornadas.
- *Alternativa para el punto 3: sacar el id del equipo de la URL del escudo*
  (`.../escudom/11.png`). Descartada: el `alt` es el nombre que da el propio sitio y se
  compara con el del mapa, que viene de la misma fuente; la URL es un detalle interno.

### D4 — Tipo de presentación propio, fuera de `BloqueAnalitico` (P5, P8)

En `analytics/presentacion.py`:

```python
@dataclass(frozen=True)
class ProximoPartido:
    rival: str
    en_casa: bool
    jornada: int
    origen: str
    capturado_en: datetime

@dataclass(frozen=True)
class ProximoRival:
    partido: ProximoPartido | None = None
    motivo: str | None = None
    # __post_init__: exactamente uno de los dos. Sin partido y sin motivo -> ValueError.
```

`JugadorPresentado` y `ClausulaPresentada` ganan el campo `proximo_rival: ProximoRival`.

- *Alternativa: meterlo en `BloqueAnalitico` como una métrica más.* Descartada porque
  `BloqueAnalitico` está atado al emparejamiento del jugador: un `no_disponible` por
  "jugador sin emparejar" se llevaría el rival por delante. Eso contradice P8 y D1.
- La invariante "sin dato = `None` + motivo" vive en el tipo, no en la plantilla
  (invariante 2).

### D5 — Dónde se juntan jugador y rival

`matching/analitica.py` gana `construir_proximo_rival(equipo_id_oficial, partidos, posicion)`, el
único sitio que cruza jugador oficial y partido scrapeado. Motivos posibles:

| Caso | Motivo |
|---|---|
| El jugador es entrenador | "los entrenadores no llevan próximo rival" (se comprueba primero) |
| Equipo oficial sin entrada en el mapa | "equipo sin correspondencia en el sitio de analítica" |
| No hay partido guardado para ese equipo | "sin datos del próximo partido de su equipo" |

(Una página rara o un final de temporada llegan aquí como "no hay partido guardado": el
parser no lo guardó.)

La `Analitica` de `servicio.py` gana `partidos: dict[str, PartidoScrapeado]`, indexado por
id de equipo de futbolfantasy:

- **Ruta normal (P4):** `recolectar_analitica_de_bd` lee la fecha más reciente de
  `proximo_partido` dentro de `MAX_ANTIGUEDAD_DIAS`, **independientemente** de que haya
  tendencias. `componer()` y `clausulas.obtener_clausulas()` llaman a
  `construir_proximo_rival` para cada jugador.
- **"Actualizar datos" (P6):** `recolectar_analitica` ya visita la página de cada equipo
  implicado. Con D2 recoge los partidos en la misma pasada. Si las tendencias fallan y
  devuelve `Analitica.vacia()`, el rival sale "sin dato" con su motivo.
- **Cron (P3):** `scrapear_todo_para_guardar` pasa de devolver una tupla de 2 a un
  dataclass `ScrapingCompleto(tendencias, probabilidades, partidos)`, para no encadenar
  tuplas posicionales. `snapshot_diario.py` guarda los partidos con
  `guardar_partidos_del_dia` (en `analitica_repo.py`, que tiene el mismo ciclo de vida).
  Si no hay partidos, la analítica se guarda igualmente (P8).

### D6 — Presentación (P4, P5, P7)

Macro nueva `celda_proximo_rival(proximo_rival)` en `macros.html`:

- Con partido: `vs Real Madrid` + insignia `Casa` / `Fuera` + `fuente: futbolfantasy.com`.
  La insignia lleva texto, no solo color ni icono.
- Sin partido: `<span class="badge-no-disponible" title="{motivo}">sin dato</span>`, el
  mismo marcado que el resto de huecos.

Columna nueva **"Próximo rival"**, con `data-etiqueta="Próximo rival"`, justo después de
"Jugador" en `_tabla_jugadores.html` (plantilla y mercado) y en `_tabla_clausulas.html`.
Estilos de la insignia en la hoja existente, con variables de color que funcionen en tema
claro y oscuro.

## Riesgos

| Riesgo | Cómo se detecta | Mitigación |
|---|---|---|
| futbolfantasy cambia el selector o los atributos | `warning` del parser; toda la columna en "sin dato" | Degrada sin tumbar nada (P2); arreglar el parser con una captura nueva |
| La sección mezcla competiciones y un cambio de rotulación cuela un partido de Copa | Test con la Copa en la fixture | Regla 2 de D3: hacen falta logo «LaLiga» **y** fase «Jornada N» |
| El dato guardado se queda viejo si el cron no corre (hasta `MAX_ANTIGUEDAD_DIAS`) | Fecha "capturado el" visible | La misma tolerancia que el resto de la analítica |
| El nombre del mapa no coincide con `data-nombre*` (por ejemplo "Rayo" frente a "Rayo Vallecano") | Test con todos los equipos del mapa en la tarea 1; `warning` en el log | Si falla para algún equipo, añadir un alias en el mapa, no relajar la comparación |
| El caso "en casa" tiene otro formato | Captura en vivo de la tarea 1 | Bloqueante de la tarea 1 |
| La migración llega tarde a producción | La web se cae al leer una tabla inexistente | Aplicar en Neon antes del merge (`pre_deploy.sh` + ensayo en `--port 8020`) |
| Romper los tests de `snapshot_diario` que hoy parchean `scrapear_todo_para_guardar` con una tupla de 2 | Suite en rojo | Se actualizan en la misma tarea que cambia la firma |
| Tabla de cláusulas demasiado ancha en escritorio (pasa a 10 columnas) | Revisión visual | Celda compacta en una línea; en móvil ya son cards |

## Orden de implementación y paralelismo

```
1. Parser + captura en vivo ──→ 2. Tabla + migración + repo ──→ 3. Cron
                          └──→ 4. Tipos de presentación + construir_proximo_rival
                                          └──→ 5. Servicio (BD + en vivo) + cláusulas
                                                         └──→ 6. Plantillas y estilos
                                                                   └──→ 7. E2E + ensayo contra Neon
```

Las ramas 2–3 y 4 son independientes entre sí. El resto va en orden.

**Puntos de verificación:** después de 1 (el parser acierta con las capturas real y en
vivo), después de 3 (la tabla se rellena con el cron parcheado), después de 6 (las tres
pantallas lo pintan en local) y después de 7 (ensayo contra producción).

## Trazabilidad y datos personales

No se tocan datos personales: son datos públicos de partidos. Se guardan `origen` y
`capturado_en` en cada fila para poder auditar, y la retención es la misma que la de
`analitica_diaria`.

## Decisiones cerradas (2026-10-18)

| Decisión | Elegido | Motivo |
|---|---|---|
| Entrenadores | **Sin próximo rival** | Decisión del usuario. Su celda sale como "sin dato" con el motivo "los entrenadores no llevan próximo rival", igual que ya pasa con su analítica: la columna existe en la tabla y no puede quedar un hueco mudo |
| Posición de la columna | **Justo después de "Jugador"** | El rival describe al jugador antes que cualquier métrica |
