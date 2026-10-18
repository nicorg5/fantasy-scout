# Design: Protección de cláusula en Mi plantilla

## Contexto

Cada jugador de `/plantilla` tiene que decir cuánto falta para que otro manager pueda
clausularlo (ver [`requirements.md`](./requirements.md)). El dato ya llega: `/plantilla`
pide `/leagues/{id}/teams/{team_id}` a LaLiga, y esa respuesta trae
`buyoutClauseLockedEndTime` e `isShielded` por jugador. Clausulazos ya lo lee de ese mismo
endpoint para los rivales (`parsear_plantilla_de_manager`); `JugadorPlantilla`, que es lo
que usa `/plantilla`, simplemente **descarta la fecha**. El trabajo consiste en
conservarla, convertirla en un estado de presentación y pintarlo.

## Requisitos que cubre

C1–C9. Cada decisión de abajo indica cuáles.

## Alcance

Incluye: el parseo de la fecha en la plantilla propia, un tipo de presentación con los
tres estados, la función de formato compartida con Clausulazos, la columna nueva en
`/plantilla` y `/plantilla/tabla`, y su CSS.

Queda fuera lo que lista `requirements.md`: el importe, ordenar o filtrar, la cuenta atrás
en vivo, los avisos fuera de la app y los cedidos. **No se tocan** la BD, las migraciones,
el cron, el scraping, la observabilidad (no hay rutas nuevas) ni `/mercado`, salvo para
pasar explícitamente "sin columna".

## Dependencias externas involucradas

- [x] `buyoutClauseLockedEndTime` en `/leagues/{id}/teams/{team_id}` → **VERIFICADO** en la
  fixture `tests/fixtures/plantilla.json` (captura real de la plantilla propia, agosto de
  2026), con zona horaria (`+02:00`), y en uso en producción por Clausulazos para los
  equipos rivales.
- [x] `isShielded` en el mismo endpoint → **VERIFICADO** en la fixture (`false` en los dos)
  y ya exigido hoy por `JugadorPlantilla`.
- [ ] Un jugador propio **blindado** → **[SIN VERIFICAR]**: la fixture no trae ninguno. Se
  cubre con un test sintético.
- [ ] Un jugador propio **sin** `buyoutClauseLockedEndTime` → **[SIN VERIFICAR]**: en la
  fixture los dos lo traen. Se trata como clausulable (supuesto 2).
- [ ] La fecha llega **siempre con zona** → **[SIN VERIFICAR]** en general, verificado en
  la fixture. Ver D1: una fecha sin zona se rechaza en vez de compararse.

No hace falta ninguna petición en vivo para diseñar ni para implementar. El ensayo contra
producción de la tarea final despejará las tres casillas pendientes, si los datos reales
lo permiten.

## Decisiones de arquitectura

### D1 — `JugadorPlantilla` conserva la fecha; el parseo se comparte (C1, C9)

`JugadorPlantilla` gana `bloqueada_hasta: datetime | None`. El parseo que hoy vive dentro
de `parsear_plantilla_de_manager` se extrae a un helper privado de `official/modelos.py`:

```python
def _fecha_de_bloqueo(crudo: dict, camino: str) -> datetime | None:
    """None si no viene; RespuestaInesperada si viene rota o sin zona."""
```

y lo usan las dos rutas de parseo, así que la plantilla propia y la de los rivales no
pueden divergir.

**Fecha sin zona → `RespuestaInesperada`.** Comparar una fecha sin zona con
`datetime.now(timezone.utc)` lanza `TypeError`, que llegaría a la ruta como un 500. Hoy
Clausulazos tiene ese mismo riesgo latente; con el helper compartido queda cubierto en las
dos pantallas. Es el único cambio de comportamiento en Clausulazos, y solo afecta a una
respuesta que hoy ya rompería la página.

Una `RespuestaInesperada` ya se traduce en el aviso de LaLiga, con 200, tanto en
`_pagina_de_jugadores` como en `_fragmento_tabla` (C9): no hay que tocar `app.py` para eso.

*Alternativa descartada:* usar `JugadorConClausula` también para la plantilla propia.
Arrastra `manager` y el sobrepago, que aquí no se usan, y obligaría a cambiar
`obtener_plantilla` y `Plantilla` enteros por un solo campo.

### D2 — Tipo `ProteccionClausula` en `presentacion.py` (C2, C4, C5)

```python
EstadoClausula = Literal["protegido", "clausulable", "blindado"]
AvisoClausula = Literal["clausulable", "pronto"]   # o None
UMBRAL_AVISO_SEGUNDOS = 24 * 3600

@dataclass(frozen=True)
class ProteccionClausula:
    estado: EstadoClausula
    segundos_restantes: float | None = None   # solo en "protegido"

    def __post_init__(self): ...              # protegido ⇔ segundos > 0

    @classmethod
    def desde_bloqueo(cls, bloqueada_hasta, blindado, ahora) -> ProteccionClausula: ...

    @property
    def legible(self) -> str: ...   # "protegido · 2d 5h" | "clausulable" | "blindado"

    @property
    def aviso(self) -> AvisoClausula | None: ...
```

Reglas, todas en el tipo y ninguna en la plantilla (invariante 2):

| Entrada | Estado | Aviso |
|---|---|---|
| `blindado=True` (con fecha o sin ella) | blindado | — |
| sin fecha, o quedan ≤ 0 s | clausulable | `clausulable` |
| quedan < 24 h | protegido | `pronto` |
| quedan ≥ 24 h | protegido | — |

`__post_init__` rechaza "protegido" sin segundos o con segundos ≤ 0, y "clausulable" o
"blindado" con segundos. Así no se puede pintar "protegido · 0m".

*Alternativa descartada:* reutilizar `ClausulaPresentada.fichable` y `espera_legible`.
Esa clase es una fila entera de Clausulazos, con manager, sobrepago y analítica; lo que se
comparte de verdad es el formato del tiempo (D3).

### D3 — Una sola función de formato (C3)

`formatear_espera(segundos: float) -> str` pasa a ser una función de módulo de
`presentacion.py`, junto a `formatear_euros`, con el cuerpo actual de
`ClausulaPresentada.espera_legible` ("2d 5h", "3h 20m", "15m"). `espera_legible` la llama
y conserva sus dos casos propios (blindado, disponible). Los tests de
`tests/test_clausulas.py` no se tocan: son la red de que el refactor no cambia nada.

### D4 — Dónde se junta jugador y protección (C2, C6, C7)

`JugadorPresentado` gana el campo `proteccion_clausula: ProteccionClausula | None`,
**obligatorio y sin valor por defecto**, por la misma razón que `proximo_rival`
(Descubrimientos T5 de `specs/proximo-rival`): con un valor por defecto, una pantalla que
olvidara rellenarlo perdería la columna sin que nada fallara.

Aquí `None` **no** es "sin dato": significa "**esta pantalla no tiene esa columna**"
(`/mercado`, donde un jugador libre no tiene cláusula). En `/plantilla` el valor nunca es
`None`, porque el tipo siempre produce uno de los tres estados. Se documenta en el
docstring del campo para que nadie lo lea como un hueco de la invariante 2.

`servicio.componer` recibe un argumento nuevo,
`protecciones: dict[str, ProteccionClausula] | None`, indexado por id oficial:

- `obtener_plantilla(..., ahora: datetime | None = None)` lo construye desde
  `plantilla.jugadores` con `ProteccionClausula.desde_bloqueo` y un `ahora` que se puede
  inyectar en los tests (si no, `datetime.now(timezone.utc)`, como en Clausulazos);
- `obtener_mercado` no lo pasa, y cada jugador lleva `None`.

El fragmento de "Actualizar datos" (`/plantilla/tabla`) llama al mismo
`obtener_plantilla`, así que recalcula la protección con la hora de ese momento (C7) sin
código propio.

### D5 — Presentación (C2, C4, C6, C8)

- `_tabla_jugadores.html` recibe en el contexto la variable `con_clausula` y pinta la
  cabecera `<th>Cláusula</th>` y la celda solo si es verdadera. La ponen a `True` los dos
  sitios que ya distinguen la plantilla del mercado: `_pagina_de_jugadores` (con la misma
  condición que `variacion`) y `_fragmento_tabla` (con `con_variacion`). Que la columna
  dependa de un flag explícito, y no de mirar los datos, evita que una plantilla vacía o
  un dato raro hagan aparecer o desaparecer la cabecera.
- La celda la pinta una macro nueva, `celda_clausula(proteccion)`, en `macros.html`, al
  estilo de `celda_proximo_rival`, con `data-etiqueta="Cláusula"` (C8) y
  `etiqueta_origen("oficial")`:
  - clausulable → píldora `clausula clausula--clausulable` con el texto "clausulable"
    (rojo, `--ll-baja`);
  - menos de 24 h → "protegido · 3h 20m" y, debajo, `<span class="meta
    clausula--pronto">menos de 24 h</span>` (ámbar, `--ll-aviso`);
  - protegido ≥ 24 h y blindado → texto atenuado, sin resaltar.
- CSS en `static/estilos.css`, junto a `.estado-fichable`, con las variables ya
  existentes (`--ll-baja`, `--ll-aviso`, `--ll-texto-tenue`). El color nunca va solo: el
  estado siempre está escrito.

## Riesgos

| Riesgo | Detección | Mitigación |
|---|---|---|
| LaLiga renombra o quita `buyoutClauseLockedEndTime` | Todos los jugadores salen "clausulable" | Lo verá el usuario de inmediato (toda la plantilla en rojo) y se anota. No se puede distinguir "campo quitado" de "sin bloqueo" sin otra fuente. |
| Fecha rota o sin zona | `RespuestaInesperada` → aviso de LaLiga | D1. Tests de C9. |
| Reloj del servidor desfasado | — | Despreciable a escala de horas; mismo criterio que Clausulazos. |
| El refactor de `espera_legible` cambia Clausulazos | `tests/test_clausulas.py` en rojo | D3: esos tests no se tocan. |
| Romper los helpers de test que construyen `JugadorPresentado` | Los tests fallan al construirlo | Son 5 sitios; se adaptan en la misma tarea (como con `proximo_rival`). |

## Orden de implementación y paralelismo

```
T1 parseo (official) ──┐
                       ├──► T3 servicio ──► T4 pantalla y CSS ──► T5 end-to-end
T2 tipo y formato ─────┘
```

T1 y T2 son independientes entre sí; T3 necesita las dos; T4 necesita T3. No hay
migraciones, así que no hay que tocar producción antes del merge.

## Trazabilidad y datos personales

No se guarda nada nuevo: la fecha se lee de LaLiga en cada petición y se descarta tras
pintarla. No hay rutas nuevas, así que la observabilidad no cambia. No se registra la
fecha en ningún log.

## Decisiones pendientes (preguntar al usuario)

Ninguna: las de producto se cerraron en `requirements.md` (2026-10-18). Los textos
("protegido · 2d 5h", "clausulable", "blindado", "menos de 24 h") se pueden ajustar en la
revisión visual de T5 sin cambiar el diseño.
