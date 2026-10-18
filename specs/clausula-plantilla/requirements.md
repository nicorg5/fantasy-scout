# Requirements: Protección de cláusula en Mi plantilla

Objetivo: **que en cada jugador de mi plantilla se vea cuánto le queda hasta que los demás
managers puedan clausularlo**, para saber a quién tengo expuesto sin salir de la app.

Numeración `C#` (para no chocar con `R#` del MVP, `O#` de observabilidad, `A#` de registro,
`S#` de registro social y `P#` de próximo rival).

## Decisiones de producto (usuario, 2026-10-18)

- "Tiempo que queda" = **hasta que se desbloquea la cláusula**: el mismo dato que
  Clausulazos enseña de los rivales, ahora para los jugadores propios.
- Va en una **columna nueva de `/plantilla`**. `/mercado` no cambia.
- El tiempo es **estático**: se calcula al servir la página, igual que en Clausulazos. Sin
  JS propio.
- **No** se muestra el importe de la cláusula y **no** se ordena por ella.
- Se resaltan los jugadores con **menos de 24 h** de protección y, con más fuerza, los que
  **ya son clausulables**. El resaltado lleva texto además de color.

## Supuestos de entrada (corrígeme ahora)

1. **Es un dato oficial**, de la API de LaLiga. Lleva la etiqueta "oficial" y no se mezcla
   con el bloque analítico scrapeado (invariante 1).
2. **Sin `buyoutClauseLockedEndTime`, o con la fecha ya pasada, el jugador es
   clausulable.** Es el criterio que Clausulazos usa hoy en producción
   (`JugadorConClausula.fichable`).
3. **`isShielded` = blindado**: nadie puede clausularlo, tenga fecha o no. El blindaje
   manda sobre la fecha.
4. **Solo la plantilla propia (`players`)**: los cedidos (`loanedPlayers`) quedan fuera,
   como ya ocurre en `/plantilla`.
5. **No hay peticiones nuevas**: el dato viene en la misma respuesta de
   `/leagues/{id}/teams/{team_id}` que `/plantilla` ya pide. No se toca la BD, el cron ni el
   scraping.

## Requisitos funcionales

- [ ] **C1**: `JugadorPlantilla` conserva la fecha de fin del bloqueo
  (`buyoutClauseLockedEndTime`) como `datetime` con zona. Si el campo no viene, es `None`;
  si viene mal formado, salta `RespuestaInesperada` (regla de `official/`: lanza ante una
  respuesta rara).
  - Verificación: tests de `Plantilla.desde_api` con `tests/fixtures/plantilla.json`
    (A. Herrero → `2026-08-21T18:01:01+02:00`), con un jugador sin el campo (→ `None`) y
    con una fecha rota (→ `RespuestaInesperada` con el camino del campo).
- [ ] **C2**: `/plantilla` muestra, para cada jugador, una columna "Cláusula" con uno de
  estos tres estados:
  - **protegido**: "protegido · 2d 5h" (bloqueo vigente, con el tiempo que falta);
  - **clausulable**: "clausulable" (sin bloqueo o ya vencido);
  - **blindado**: "blindado".
  Nunca aparece un hueco, un guion ni un `0`.
  - Verificación: test de rutas con la hora fijada y jugadores en los tres estados → el
    HTML de cada fila contiene su texto.
- [ ] **C3**: el tiempo usa **el mismo formato que Clausulazos** ("2d 5h", "3h 20m",
  "15m"), producido por **una sola función** compartida por las dos pantallas.
  - Verificación: test unitario de los límites (más de un día, menos de un día, menos de
    una hora); los tests de `tests/test_clausulas.py` siguen en verde sin tocarlos.
- [ ] **C4**: resaltado:
  - menos de 24 h de protección → aviso **"menos de 24 h"** y clase de aviso moderado;
  - clausulable → clase de aviso fuerte (el propio texto "clausulable" ya lo dice);
  - más de 24 h o blindado → sin resaltar.
  - Verificación: test de rutas con jugadores a 23 h, a 25 h, vencido y blindado → solo los
    dos primeros casos citados llevan su clase, y el de 23 h lleva el texto "menos de 24 h".
- [ ] **C5**: el estado vive en un tipo de `analytics/presentacion.py` que no se puede
  construir de forma incoherente: "protegido" sin tiempo, o "clausulable" o "blindado" con
  tiempo, lanza `ValueError` (invariante 2: la regla vive en el tipo, no en la plantilla).
  Si a la fecha le quedan 0 s o menos, el jugador es clausulable, nunca "protegido · 0m".
  - Verificación: tests de `presentacion` de las combinaciones inválidas y del límite
    exacto.
- [ ] **C6**: `/mercado` **no** muestra la columna (en una subasta la cláusula no aplica).
  - Verificación: test de rutas: el HTML de `/mercado` no contiene la cabecera "Cláusula".
- [ ] **C7**: el botón "Actualizar datos" (fragmento `/plantilla/tabla`) devuelve la tabla
  con la columna, recalculada en ese momento.
  - Verificación: test del fragmento `/plantilla/tabla?en_vivo=…` → contiene la columna y
    el estado esperado.
- [ ] **C8**: en móvil (vista de tarjetas), la celda se lee con su etiqueta, como el resto.
  - Verificación: la celda lleva `data-etiqueta="Cláusula"` (test); comprobación manual en
    el navegador a ancho de móvil.
- [ ] **C9**: una fecha de bloqueo rota en la respuesta de LaLiga se trata como cualquier
  otro fallo de la API oficial: 200 con el aviso actual ("No se ha podido contactar con
  LaLiga Fantasy…"), nunca un 500.
  - Verificación: test de rutas de `/plantilla` y `/plantilla/tabla` con la fecha rota.

## Dependencias aún no disponibles

- **Jugador propio blindado** `[SIN VERIFICAR]`: la fixture no trae ninguno. Se apoya en
  que Clausulazos lee `isShielded` del mismo endpoint en producción. Se cubre con un test
  sintético; si al ensayar contra producción aparece un blindado, se anota en
  `## Descubrimientos`.
- **Jugador propio sin `buyoutClauseLockedEndTime`** `[SIN VERIFICAR]`: en la fixture los
  dos lo traen. El supuesto 2 lo cubre (→ clausulable).

## Fuera de alcance (explícito)

- Mostrar el importe de la cláusula o el sobrepago.
- Ordenar o filtrar la plantilla por cláusula.
- Cuenta atrás en vivo en el navegador.
- Avisos fuera de la app (email, notificaciones) cuando un jugador pasa a ser clausulable.
- Cedidos (`loanedPlayers`).
- Subir o modificar la cláusula desde la app.
- Cambios en Clausulazos, salvo compartir la función de formato (C3).
