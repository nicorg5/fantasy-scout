# Tasks: Alta de usuarios por invitación

Catorce tareas en cinco bloques. Ninguna se marca hecha sin ejecutar su verificación.
Ver [`requirements.md`](./requirements.md) y [`design.md`](./design.md).

Leyenda: **[DATO]** = necesita algo que solo puedes aportar tú. **[PROD]** = toca
producción. **[TÚ]** = punto de control: me paro y espero tu confirmación.

Reglas de trabajo (memoria del repo): se trabaja en `feature/v0.1.0`; **commits solo al
final**, tras el end-to-end verificado, sin línea `Co-Authored-By` y con cuerpo breve; el
merge a `main` lo decides tú.

```
1. CIMIENTOS ──► 2. INVITACIONES ──► 3. ALTA ──► 4. LOGIN Y RECONEXIÓN ──► 5. CIERRE
  T1..T4            T5, T6            T7, T8        T9..T11                  T12..T14
```

El orden es de dependencia real: el alta (T7) necesita invitaciones (T5), stores sin
commit (T4) y email normalizado (T1); la pantalla de admin (T6) necesita la autorización
nueva (T3). El bloque 4 no depende del 2 ni del 3 y podría ir antes, pero va después para
que el ensayo del alta ejercite también el login resincronizado.

---

## Bloque 1 — Cimientos

- [x] **T1**: `normalizar_email()` en `passwords.py` y su uso en `procesar_login`,
  `scripts/crear_usuario.py` y la lectura de `admin_email` en `config.py`.
  - Cubre: A15 (parte de código).
  - Ficheros: `auth/passwords.py`, `auth/rutas.py`, `scripts/crear_usuario.py`,
    `config.py`, `tests/test_auth.py`.
  - Bloqueante: ninguno.
  - Verificación: test de login con `Pepe@X.com ` contra `pepe@x.com`; suite en verde.

- [x] **T2**: Modelo `Invitacion` y migración Alembic: tabla según D2 y normalización de
  emails existentes que **aborta** si hay duplicados (D5).
  - Cubre: A1, A15 (parte de datos).
  - Ficheros: `storage/modelos.py`, `migrations/versions/<rev>_invitacion.py`,
    `tests/test_invitaciones.py` (nuevo).
  - Bloqueante: ninguno.
  - Verificación: ciclo `upgrade → downgrade → upgrade` en local; `\d invitacion`; test que
    siembra `A@x.com` y `a@x.com` y comprueba que la migración falla sin tocar nada.

- [x] **T3**: `auth/admin.py` con `es_admin`, `administrador` y `NoAdministrador` movidos
  desde `observabilidad/rutas.py`; handler en `app.py` que responde **302 → `/plantilla`**;
  `/invitaciones` en `RUTAS_IGNORADAS`.
  - Cubre: A3 (sobre `/uso`), D1, D7.
  - Ficheros: `auth/admin.py` (nuevo), `observabilidad/rutas.py`, `observabilidad/registro.py`,
    `api/app.py`, `tests/test_uso.py`.
  - Bloqueante: ninguno.
  - Verificación: los tests de `/uso` que esperaban 404 pasan a esperar 302 a `/plantilla`
    (usuario normal) y 302 a `/login` (sin sesión); con `FANTASY_ADMIN_EMAIL` vacía nadie
    pasa. Suite en verde.

- [x] **T4**: Separar escritura y commit en los stores: `_escribir_credenciales` y
  `_escribir_token` sin commit; `guardar_credenciales` y `guardar_token` las envuelven.
  Constante para el `ultimo_error` de credenciales rechazadas (D10).
  - Cubre: D4 (sin cambio de comportamiento).
  - Ficheros: `auth/credenciales_store.py`, `auth/token_store.py`.
  - Bloqueante: ninguno.
  - Verificación: `test_token_store.py` y `test_login_automatico.py` en verde sin tocarlos.

## Bloque 2 — Invitaciones

- [x] **T5**: `auth/invitaciones.py`: `crear()` (devuelve el token en claro una sola vez),
  `estado()` pura, `buscar_para_alta()` con `FOR UPDATE`, `revocar()`, `listar()`.
  - Cubre: A4–A6 (lógica).
  - Ficheros: `auth/invitaciones.py` (nuevo), `tests/test_invitaciones.py`.
  - Bloqueante: T2.
  - Verificación: tests de caducidad a 7 días, `token_hash == sha256(token)`, longitud del
    token ≥ 43 caracteres, los cuatro estados y su prioridad, revocar una no pendiente no
    hace nada.

- [x] **T6**: Rutas `GET/POST /invitaciones` y `POST /invitaciones/{id}/revocar`,
  plantilla `invitaciones.html` con enlace + botón Copiar, menú con **"Invitación"** (solo
  admin) y **"Token" → "Cuenta"**. Enlace construido según D8.
  - Cubre: A2, A3 (sobre `/invitaciones`), A4–A6 (pantalla).
  - Ficheros: `auth/rutas.py`, `api/templates/invitaciones.html` (nuevo),
    `api/templates/base.html`, `tests/test_invitaciones.py`.
  - Bloqueante: T3, T5.
  - Verificación: tests de A2 (el HTML de un usuario normal no contiene `/invitaciones` ni
    `/uso`), A3 en GET y POST, A4 (el enlace aparece y empieza por `https://` fuera de
    local), A5 (una invitación en cada estado), A6.

## Bloque 3 — Alta

- [x] **T7**: `auth/alta.py` con `dar_de_alta()` y las excepciones `EnlaceNoValido` y
  `YaTieneCuenta`, según el flujo de D3.
  - Cubre: A9–A13 (lógica).
  - Ficheros: `auth/alta.py` (nuevo), `tests/test_alta.py` (nuevo).
  - Bloqueante: T1, T4, T5.
  - Verificación, con `laliga_login.iniciar_sesion` parcheado:
    - alta correcta: usuario, credenciales, token e invitación usada, en una transacción;
    - fallo forzado en la última escritura: no queda nada;
    - `CredencialesInvalidas` / `LoginNoDisponible`: nada creado, invitación pendiente;
    - email existente: `iniciar_sesion` no se llama;
    - **A13**: dos hilos con dos sesiones contra Postgres real, un solo usuario creado.

- [x] **T8**: Rutas `GET/POST /registro` y plantilla `registro.html` (formulario o "enlace
  no válido"). Sustituir `test_no_hay_endpoint_de_registro`.
  - Cubre: A7, A8, A9 (cookie y 302), A10–A12 (páginas y códigos), A14.
  - Ficheros: `auth/rutas.py`, `api/templates/registro.html` (nuevo),
    `tests/test_alta.py`, `tests/test_auth.py`.
  - Bloqueante: T7.
  - Verificación: tests de A7; A8 parametrizado (5 casos × GET/POST); alta correcta deja
    cookie válida y 302 a `/plantilla`; códigos 400/503; **A14** con `caplog` y consulta a
    `evento_uso` (ni contraseña ni token en ninguna parte).

## Bloque 4 — Login y reconexión

- [x] **T9**: Resincronización en `procesar_login` según D6.
  - Cubre: A16, A17.
  - Ficheros: `auth/rutas.py`, `auth/credenciales_store.py`, `tests/test_auth.py`.
  - Bloqueante: T1, T4.
  - Verificación: tests de los cinco caminos de D6 comprobando cuándo se llama a
    `iniciar_sesion` y que tras resincronizar el hash y las credenciales son los nuevos.

- [x] **T10**: `CredencialesRechazadas(TokenInvalido)`, `credenciales_rechazadas()` y
  mensajes de aviso nuevos en `_pagina_de_jugadores`, `_fragmento_tabla` y `/clausulas`.
  - Cubre: A20, D10.
  - Ficheros: `official/errores.py`, `official/cliente.py`, `auth/credenciales_store.py`,
    `api/app.py`, `tests/test_rutas.py`.
  - Bloqueante: T4.
  - Verificación: `/plantilla`, `/plantilla/tabla` y `/clausulas` con credenciales
    rechazadas muestran "Tu contraseña de LaLiga ha cambiado" con enlace a `/token`, código
    200; con token caducado sin credenciales, el mensaje nuevo sin tecnicismos.

- [x] **T11**: `token.html` → "Tu cuenta de LaLiga", bloque manual en `<details>`, y
  formulario de reconexión cuando las credenciales están rechazadas.
  - Cubre: A18, A19.
  - Ficheros: `api/templates/token.html`, `auth/rutas.py`, `tests/test_pantalla_token.py`.
  - Bloqueante: T10.
  - Verificación: tests del HTML (título, `<details>` sin `open`, formulario visible con
    credenciales rechazadas).

## Bloque 5 — Cierre

- [x] **T12**: Coherencia del repo: R6 y §Modelo de auth en `specs/mvp/`, `README.md`
  (alta por la pestaña Invitación; `crear_usuario.py` queda como puerta trasera y se indica
  el `--env-file .env.produccion`), `CLAUDE.md`.
  - Cubre: A21.
  - Bloqueante: T1–T11.
  - Verificación: revisión del diff; `uv run pytest` en verde.

- [x] **T13** **[TÚ]**: End-to-end en local. `bash scripts/pre_deploy.sh` y la app en local
  contra el Postgres de Docker: creo una invitación como admin, la abro en ventana privada y
  completo el alta. Revisión visual de `/invitaciones`, `/registro` y `/token` en móvil y
  escritorio.
  - Cubre: A22 (parte local).
  - Bloqueante: T12. **[DATO]** una cuenta real de LaLiga distinta de la tuya para el alta
    (sin ella, el e2e local se hace con tu cuenta en una BD local vacía).
  - Verificación: el nuevo usuario ve su plantilla; tú confirmas la revisión visual.

- [x] **T14** **[PROD]** **[TÚ]**: Producción.
  1. Te pido permiso y aplico `alembic upgrade head` contra Neon (**antes del merge**).
  2. Ensayo contra Neon en `--port 8020`: crear invitación, comprobar el esquema y host del
     enlace (D8) y completar el alta con la cuenta de T13.
  3. Commits en `feature/v0.1.0`; el merge a `main` lo haces tú o me lo pides.
  4. Tras el despliegue: crear una invitación real desde Render y comprobar que el enlace
     empieza por `https://fantasy-scout.onrender.com`.
  - Cubre: A22.
  - Bloqueante: T13. **[DATO]** la misma cuenta de LaLiga de T13.
  - Verificación: el usuario creado en el ensayo ve su plantilla en producción; si era una
    cuenta de prueba, se borra al terminar (con tu confirmación).

---

## Descubrimientos

### T1 — `config.py` no usa `normalizar_email`
`config.py` es la capa más baja (todo importa de ella); importar `auth/` desde ahí invertiría
la dependencia. Ya hacía `.strip().lower()`, que es exactamente lo mismo. Se deja así.

### T4 — Las funciones sin commit son públicas (`escribir_*`), no `_escribir_*`
Las usa `auth/alta.py`, otro módulo: importar nombres con guion bajo entre módulos rompe la
convención. Quedan `escribir_token`, `escribir_credenciales` y `escribir_tokens`, con
docstring "sin confirmar". Las públicas de siempre siguen igual y sus tests no se tocaron.

### T8 — El token de la invitación sí aparece en logs, pero solo del lado del cliente
El test de A14 lo encontró en el log de `httpx`: es el **cliente de pruebas** anotando la URL
que pide, el equivalente al log de acceso de Render (riesgo ya aceptado en design.md). Ningún
log de la app lo contiene, ni la contraseña aparece en ningún log. El test lo distingue.

### T7 — El test de concurrencia se comprobó en rojo
Quitando el `FOR UPDATE`, dos altas simultáneas con el mismo enlace crean **dos** usuarios
(`['ok', 'ok']`). Con él, una sola. El test detecta la regresión de verdad.

### T6 — Refrescar tras crear una invitación crea otra
`POST /invitaciones` responde con la página (el enlace solo existe en esa respuesta), así que
F5 reenvía el formulario. Inofensivo: queda otra invitación pendiente que se puede revocar.
Se deja así; un PRG perdería el enlace.

### T13/T14 — `FANTASY_ADMIN_EMAIL` no está en `.env` ni en `.env.produccion`
Solo vive en el dashboard de Render. Sin ella en el fichero, en local y en el ensayo contra
Neon nadie es admin y no aparece la pestaña Invitación. Hay que añadirla a ambos antes de
T13 y T14.

### Suite
De 249 a **314 tests**, todos en verde. `ruff --select F` limpio en todo lo tocado (quedan 12
avisos anteriores en ficheros ajenos a este feature).

### T14 — Ensayo contra Neon correcto; se añade A23
Migración aplicada en Neon (`1a17fe9d3646 → 2f3a087ef0d2`) el 2026-09-27, con permiso. El
ensayo en `:8020` fue bien (login, menú de admin, Cuenta, crear y revocar invitación). Tras
él, el usuario pidió ver cuándo se da de alta cada cuenta: A23, sin migración.

### Despliegue (2026-09-27) y primera alta real
Desplegado en Render. Un amigo de **otra liga** se dio de alta desde su iPhone y ve su
plantilla y su mercado: todas las pantallas leen la liga del usuario que navega.

---

## Investigación: alta de cuentas de LaLiga creadas con Google (2026-09-29 → 2026-10-12)

**Problema:** las cuentas de LaLiga creadas con Google no tienen contraseña, así que el login
ROPC (`B2C_1A_ResourceOwnerv2`) no les sirve, y LaLiga no les deja crear una.

**Hallazgos verificados:**
- La política interactiva `B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN` acepta nuestro cliente
  (`af88bcff…`) con código de autorización + PKCE y ofrece Google, Apple, Facebook, Amazon,
  Instagram, Twitch y Twitter. La de ROPC no ofrece ninguno.
- Direcciones de vuelta registradas para ese cliente (sondeadas con GET, 2026-10-11):
  `authredirect://com.lfp.laligafantasy`, **`https://jwt.ms`** y `urn:ietf:wg:oauth:2.0:oob`.
  `fantasy-scout.onrender.com`, `localhost` y `miliga.laliga.com` → `AADB2C90006`.
- `https://jwt.ms` con `response_mode=fragment` deja el código en la barra de direcciones de
  Safari, copiable en un iPhone sin herramientas de desarrollador. Probado con un amigo real.
- Canje y renovación correctos (`specs/registro/spike_google.py`): `idp: google.com`, la API
  devuelve su liga, el token trae `email`, `refresh_token_expires_in: 7776000` (90 días) y el
  refresh token **rota** en cada renovación.
- B2C exige `nonce` en este flujo. `scope=openid offline_access` basta; el bearer es el
  `id_token`, como en ROPC.
- Con una cuenta de email y contraseña (la del admin), entrar con Google da **400** en
  `oauth2/authresp`: esa identidad de Google no está asociada a ninguna cuenta. No es un fallo
  del flujo: hay que probar siempre con una cuenta que sea de verdad de Google.

**Sin verificar:**
- Si existe un tope total de sesión desde el primer login (la ventana deslizante de B2C, que
  por defecto es de 90 días). Si existe, habrá que repetir el paso de Google cada 3 meses.
- Que LaLiga mantenga `https://jwt.ms` registrado. Si lo quita, la vía cae (las cuentas ya
  creadas siguen mientras viva su refresh token).

**Descartado:** `miliga.laliga.com` (cliente web: refresh tokens de SPA, 24 h);
`authredirect://` en iPhone (Safari no deja ver la dirección); pegar el token a mano.
