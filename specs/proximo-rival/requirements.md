# Requirements: Próximo rival y casa/fuera

Objetivo: **que en cada fila de jugador se vea contra quién juega su equipo en la próxima
jornada y si es en casa o fuera**, para decidir alineaciones y fichajes sin salir de la app.

Numeración `P#` (para no chocar con `R#` del MVP, `O#` de observabilidad, `A#` de registro).

## Supuestos de entrada (corrígeme ahora)

1. **El rival es un dato del equipo, no del jugador.** Todos los jugadores de un mismo
   equipo comparten rival y condición de local o visitante.
2. **"Próximo partido" es el primero de LaLiga en la sección «Próximos partidos» de la
   página del equipo.** No calculamos nosotros qué partido toca. (Cambiado el 2026-10-18,
   ver `design.md` §D3: el selector de alineaciones que se pensó al principio sigue
   señalando el partido ya jugado hasta que sale el once siguiente.)
3. **Es dato scrapeado**: va en el bloque analítico, con su origen "futbolfantasy.com" y la
   fecha de captura, nunca mezclado con el dato oficial (invariante 1).
4. **No hay peticiones nuevas a futbolfantasy**: se saca de la misma página de equipo que
   el cron ya descarga. Así no hace falta revisar robots.txt para ninguna ruta nueva.
5. **El rival se muestra con su nombre corto** tal y como lo da futbolfantasy ("Real
   Madrid", "Rayo"), no con la abreviatura ("RMD").
6. **Solo LaLiga**: partidos de Copa o de competiciones europeas no cuentan como "próximo
   rival".

## Requisitos funcionales

- [ ] **P1** — El parser de la página de equipo devuelve el próximo partido de LaLiga del
  equipo: nombre del rival, en casa / fuera y jornada. (La abreviatura se quitó el
  2026-10-18: esa sección no la trae y la pantalla no la usaba.)
  - Verificación: test con `tests/fixtures/scraping_equipo.html`, ampliada con la sección
    recortada de la captura real del Málaga del 2026-08-27: rival "Real Madrid", fuera,
    jornada 3.
- [ ] **P2** — Si la página no trae el partido o tiene otro formato, el parser devuelve
  "sin dato" con motivo y **no lanza** (regla de `scrapers/`).
  - Verificación: test con HTML sin la sección «Próximos partidos», sin partidos de
    LaLiga o con atributos rotos → `None` + aviso en el log, ninguna excepción.
- [ ] **P3** — El cron guarda el próximo rival de cada equipo junto con la analítica del
  día, sin hacer peticiones nuevas.
  - Verificación: test de `snapshot_diario` parcheado: tras ejecutarse, la BD tiene el
    rival y casa/fuera de los jugadores de ese equipo; el número de peticiones HTTP
    parcheadas es el mismo que antes del cambio.
- [ ] **P4** — `/plantilla`, `/mercado` y `/clausulas` muestran para cada jugador
  "vs {rival}" y la etiqueta "Casa" o "Fuera", con el origen "futbolfantasy.com".
  - Verificación: test de rutas con datos sembrados: el HTML de las tres pantallas
    contiene "Real Madrid" y "Fuera" en la fila del jugador del Málaga.
- [ ] **P5** — Si no hay dato de rival para un jugador (fallo de scraping, jugador sin
  emparejar, equipo sin partido próximo) se muestra "sin dato" con motivo, nunca un hueco,
  un guion ni un rival inventado (invariante 2, en el tipo de `presentacion.py`).
  - Verificación: test de `presentacion` que no deja construir el bloque con rival `None`
    sin motivo; test de rutas con un jugador sin rival → texto "sin dato".
- [ ] **P6** — El botón "Actualizar datos" (`en_vivo=True`) refresca también el rival.
  - Verificación: test de `/plantilla/tabla?en_vivo=…` con el scraping parcheado
    devolviendo otro rival → el fragmento muestra el nuevo.
- [ ] **P7** — En móvil (vista en cards) el rival se lee con su etiqueta como el resto de
  columnas (`data-etiqueta`).
  - Verificación: la celda nueva lleva `data-etiqueta="Próximo rival"`; comprobación
    manual en el navegador a ancho de móvil.
- [ ] **P8** — Un fallo al sacar el rival no afecta a la tendencia ni a la probabilidad
  del mismo jugador: cada métrica tiene su propia disponibilidad.
  - Verificación: test con la sección de partidos rota pero el widget de probabilidad intacto →
    probabilidad presente, rival "sin dato".

## Dependencias aún no disponibles

- ~~Formato de "en casa"~~: **verificado el 2026-10-18** con la página en vivo del Málaga
  (jornada 12, Málaga - Racing).
- **Final de temporada** `[SIN VERIFICAR]`: suponemos que la sección se queda sin partidos
  de LaLiga. P5 lo cubre mostrando "sin dato".

## Fuera de alcance (explícito)

- Dificultad del rival, fecha y hora del partido, escudos.
- Varios partidos por delante (calendario).
- Obtener el rival de la API oficial o contrastar ambas fuentes.
- Refrescar el rival en los disparos de tarde del cron.
- Partidos de Copa o de competiciones europeas.
- Ordenar o filtrar las tablas por rival o por casa/fuera.


