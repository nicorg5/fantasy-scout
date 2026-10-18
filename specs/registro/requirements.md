# Requirements: Alta de usuarios por invitación

Objetivo del feature: **que un amigo se dé de alta solo, a partir de un enlace que le mando,
introduciendo únicamente su email y contraseña de LaLiga Fantasy**, y que al terminar vea su
plantilla sin pasar por `/token` ni saber qué es un token.

Numeración `A#` para no chocar con los `R#` del MVP ni los `O#` de observabilidad. Cada
requisito tiene verificación concreta. Ver decisiones en [`design.md`](./design.md) y
desglose en [`tasks.md`](./tasks.md).

---

## Supuestos de entrada (corrígeme ahora)

1. **Sigue siendo un grupo cerrado** (~10 usuarios). Sin invitación válida no hay alta:
   compartir la URL de la app no abre la puerta. Por eso el aviso de privacidad sigue fuera
   de alcance (ver `specs/observabilidad/requirements.md`, decisión "Avisar a los usuarios").
2. **La invitación no va ligada a un email.** Quien tenga el enlace puede usarlo, una vez.
3. **El enlace completo solo se ve al crearlo.** En la base de datos se guarda el hash del
   token, no el token: si pierdes el enlace, revocas y creas otro. Es lo que impide que una
   fuga de la base de datos deje enlaces utilizables.
4. **La contraseña de fantasy-scout es la de LaLiga.** Se guarda dos veces con propósitos
   distintos: hash Argon2 en `user` (para el login de la app, que no depende de que LaLiga
   responda) y cifrado reversible en `laliga_credentials` (para renovar el token).
5. **El admin se sigue identificando por `FANTASY_ADMIN_EMAIL`**, sin columna nueva.
6. **El alta no pide casilla de consentimiento**: un texto informativo explica que las
   credenciales se guardan cifradas. Suficiente para un grupo cerrado de confianza.
7. **Esta spec va fuera de git**, como `specs/mvp/` y `specs/observabilidad/`.

---

## Decisiones cerradas (2026-09-27)

| Decisión | Elegido | Motivo |
|---|---|---|
| Registro público o por invitación | **Invitación de un solo uso** | La URL de `onrender.com` es descubrible; abrir el alta obligaría a resolver antes privacidad y límite de intentos |
| Credenciales de la app | **Las mismas que en LaLiga** | Una sola contraseña para gente poco técnica. Separarlas no resuelve el caso Google/Apple: la app necesita igualmente una contraseña de LaLiga para obtener el token |
| Varias ligas | **Se mantiene `ligas[0]`** | Se aborda en el futuro según cómo escale la app |
| Dónde se crean las invitaciones | **Pestaña "Invitación"**, solo admin | Usable desde el móvil, sin terminal ni `.env.produccion` |
| Caducidad de la invitación | **7 días** | |
| No admin que teclea `/uso` o `/invitaciones` | **302 a `/plantilla`**, sin mensaje | El usuario no debe ver ni un 404. Cambia el 404 actual de `/uso` |
| Cuentas de LaLiga con Google/Apple | **Mensaje con instrucciones**; solución completa aplazada | Ver "Dependencias" |

---

## Bloque A — Invitaciones (admin)

- [ ] **A1**: Existe la tabla `invitacion` con, como mínimo: `id`, `token_hash` (único),
  `creada_en`, `caduca_en`, `usada_en` (nullable), `usuario_id` (FK a `user`, `ON DELETE SET
  NULL`, nullable), `revocada_en` (nullable). Ninguna columna guarda el token en claro.
  - Verificación: `uv run alembic upgrade head` y `\d invitacion` en psql; revisión del
    modelo confirma que no hay columna con el token en claro.
- [ ] **A2**: La pestaña **"Invitación"** (`/invitaciones`) aparece en el menú solo para el
  admin, igual que "Uso".
  - Verificación: test que renderiza una página como admin (el enlace aparece) y como
    usuario normal (el HTML no contiene `/invitaciones` ni `/uso`).
- [ ] **A3**: Un usuario autenticado que no es admin y pide `/uso` o `/invitaciones` (GET o
  POST) recibe **302 a `/plantilla`**. Sin sesión, 302 a `/login` como cualquier ruta. Con
  `FANTASY_ADMIN_EMAIL` vacía nadie pasa.
  - Verificación: tests de las tres situaciones sobre ambas rutas.
- [ ] **A4**: El botón "Crear invitación" genera una invitación que caduca a los **7 días**
  y muestra el enlace completo (`<base>/registro?invitacion=<token>`) con un botón de copiar.
  El token tiene al menos 128 bits de entropía.
  - Verificación: test que crea una invitación, comprueba `caduca_en - creada_en = 7 días`,
    que la respuesta contiene el enlace y que `token_hash` es el SHA-256 del token mostrado.
- [ ] **A5**: La página lista las invitaciones con su estado — **pendiente**, **usada**
  (con el email de quien la usó), **caducada** o **revocada** — de más reciente a más antigua.
  - Verificación: test con una invitación en cada estado.
- [ ] **A6**: Una invitación pendiente se puede **revocar**; a partir de ahí no sirve para
  darse de alta.
  - Verificación: test que revoca y luego intenta el alta (ver A8).

## Bloque B — Alta

- [ ] **A7**: `GET /registro?invitacion=<token>` con invitación pendiente muestra un
  formulario con **email de LaLiga Fantasy** y **contraseña de LaLiga Fantasy**, más un texto
  que explica que se guardan cifradas y por qué.
  - Verificación: test de la respuesta 200 con ambos campos.
- [ ] **A8**: Con invitación inexistente, caducada, usada o revocada, o sin parámetro, la
  página muestra **un único mensaje** ("Este enlace ha caducado o ya se ha usado; pide otro a
  quien te lo mandó") y **ningún formulario**. El mensaje no distingue el motivo. El `POST`
  en esos casos tampoco crea nada.
  - Verificación: test parametrizado de los cinco casos, en GET y en POST.
- [ ] **A9**: Alta correcta: si LaLiga acepta las credenciales, **en una sola transacción**
  se crea el usuario (email en minúsculas, hash Argon2 de la contraseña), se guardan las
  credenciales cifradas y el token, y se marca la invitación como usada con su `usuario_id`.
  Se abre la sesión (misma cookie que `/login`) y se responde **302 a `/plantilla`**.
  - Verificación: test con `laliga_login.iniciar_sesion` parcheado; comprueba las cuatro
    escrituras, la cookie y la redirección. Otro test hace fallar la última escritura y
    comprueba que no queda ninguna de las anteriores.
- [ ] **A10**: Si LaLiga **rechaza** las credenciales: 400, mensaje en lenguaje llano que
  menciona el caso Google/Apple ("crea una contraseña en laligafantasy.com con 'He olvidado
  mi contraseña' y vuelve a este mismo enlace; si no funciona, escribe a quien te invitó"),
  **no se crea nada y la invitación sigue pendiente**.
  - Verificación: test con `CredencialesInvalidas` parcheado.
- [ ] **A11**: Si el login de LaLiga **no responde**: 503, mensaje "inténtalo en un rato",
  **no se crea nada y la invitación sigue pendiente**.
  - Verificación: test con `LoginNoDisponible` parcheado.
- [ ] **A12**: Si el email (en minúsculas) **ya tiene cuenta**: mensaje con enlace a
  `/login`, **no se llama a LaLiga**, no se crea nada y la invitación sigue pendiente.
  - Verificación: test que comprueba que `iniciar_sesion` no se llamó.
- [ ] **A13**: Dos altas simultáneas con la misma invitación crean **exactamente un**
  usuario.
  - Verificación: test con dos sesiones de BD y dos hilos contra Postgres real.
- [ ] **A14**: Ni la contraseña ni el token de invitación aparecen en logs de la app, en
  `evento_uso` ni en ninguna respuesta HTML (salvo el enlace en la pantalla del admin, A4).
  - Verificación: test con `caplog` sobre un alta correcta y otra fallida; consulta a
    `evento_uso` tras el alta (la ruta se guarda sin query string, ver O1).

## Bloque C — Login

- [ ] **A15**: El email se normaliza a **minúsculas y sin espacios** en el login, en el alta
  y en `scripts/crear_usuario.py`. La migración de A1 pasa a minúsculas los emails existentes
  y **aborta** si eso produjera un duplicado.
  - Verificación: test de login con `Pepe@X.com` contra una cuenta `pepe@x.com`; test de la
    migración con un duplicado forzado. En producción hoy: 0 emails con mayúsculas
    (comprobado el 2026-09-27).
- [ ] **A16**: **Resincronización**: si la contraseña no casa con el hash local, el usuario
  tiene credenciales de LaLiga guardadas y LaLiga **acepta** esa contraseña, se actualizan el
  hash local y las credenciales cifradas y el login entra. Cubre el caso "cambié la
  contraseña en LaLiga".
  - Verificación: test con `iniciar_sesion` parcheado aceptando; tras el login, el hash
    nuevo verifica la contraseña nueva y las credenciales descifradas son las nuevas.
- [ ] **A17**: Si LaLiga rechaza o no responde en ese intento, el login falla con el mismo
  401 genérico de hoy y no se modifica nada. A un email inexistente o a un usuario **sin**
  credenciales guardadas **no se le llama nunca a LaLiga**.
  - Verificación: tests de los cuatro casos, comprobando llamadas a `iniciar_sesion`.

## Bloque D — Reconexión sin tecnicismos

- [ ] **A18**: `/token` pasa a titularse **"Tu cuenta de LaLiga"**. Lo visible es solo el
  formulario de conexión (o el estado "conectada"); la opción de pegar el token queda dentro
  de un `<details>` cerrado llamado "Opción avanzada". Nada se elimina: es el respaldo si
  LaLiga cierra el login programático.
  - Verificación: test del HTML (título, `<details>` sin `open`).
- [ ] **A19**: Si las credenciales guardadas han sido **rechazadas** por LaLiga
  (`ultimo_error` de rechazo), `/token` muestra el formulario para introducir la contraseña
  nueva, no el estado "conectada".
  - Verificación: test con una fila de credenciales con ese `ultimo_error`.
- [ ] **A20**: En ese mismo caso, el aviso de las páginas de datos dice **"Tu contraseña de
  LaLiga ha cambiado: vuelve a conectarla"** con enlace a `/token`, en lugar del aviso de
  token caducado. Sigue siendo 200, nunca 500.
  - Verificación: test de `/plantilla` y de `/plantilla/tabla` en ese estado.

- [x] **A23** *(añadido el 2026-09-27, tras el ensayo)*: **Uso** muestra la fecha de **alta**
  de cada cuenta (`user.creado_en`) y **Invitación** muestra **cuándo** se usó cada enlace
  (`invitacion.usada_en`), además de quién. Sin migración: los datos ya existían.
  - Verificación: `test_el_admin_ve_el_panel`, `test_la_lista_muestra_cada_estado_y_quien_la_uso`
    y el test de `detalle_por_usuario`.

## Bloque E — Coherencia del repo

- [ ] **A21**: R6 de `specs/mvp/requirements.md` y `design.md §Modelo de auth` se reescriben
  para reflejar el alta por invitación; `test_no_hay_endpoint_de_registro` se sustituye por
  "sin invitación válida no hay alta". `README.md` y `CLAUDE.md` mencionan la pestaña
  Invitación como la forma de dar de alta.
  - Verificación: revisión del diff; `uv run pytest` en verde.
- [ ] **A22**: `bash scripts/pre_deploy.sh` en verde y ensayo contra Neon (puerto 8020):
  creo una invitación, se completa un alta con una cuenta real de LaLiga distinta de la mía y
  el nuevo usuario ve su plantilla.
  - Verificación: manual, con el usuario delante. Ver "Dependencias".

---

## Dependencias aún no disponibles

- **Una segunda cuenta real de LaLiga** para A22. La pones tú (un amigo o una cuenta de
  prueba). Hasta entonces, el alta se verifica con LaLiga parcheado en los tests.
- **Cuentas de LaLiga que entran con Google/Apple.** Dato real (2026-09-27): el admin entra
  en LaLiga con Google y aun así su email y contraseña funcionan en el login programático,
  así que al menos algunas de esas cuentas tienen contraseña. No está comprobado que valga
  para todas. Mientras tanto, A10 da instrucciones. Si un amigo real se atasca, se abre una
  investigación acotada del flujo OAuth con refresh token (ver conversación del 2026-09-27).

## Fuera de alcance (explícito)

- Registro abierto sin invitación.
- Invitación ligada a un email concreto, o de varios usos.
- Elegir liga cuando el usuario tiene varias.
- Cambiar o recuperar la contraseña al margen de LaLiga (la resincronización de A16 cubre el
  cambio hecho en LaLiga).
- Baja de usuarios, revocar la sesión de un usuario concreto, límite de intentos de login.
- Aviso de privacidad y base legal (sigue siendo un grupo cerrado).
- Emails de invitación: el enlace lo mandas tú por el canal que quieras.
