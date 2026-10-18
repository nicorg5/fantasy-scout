# Design: Alta de usuarios por invitación

## Contexto

Hoy las cuentas se crean a mano con `scripts/crear_usuario.py` contra Neon, se reparte la
contraseña por otro canal y el usuario tiene que conectar LaLiga en `/token`, una pantalla
pensada para alguien técnico. Se quiere que un amigo reciba un enlace, ponga su email y
contraseña de LaLiga y aterrice en su plantilla funcionando. Ver
[`requirements.md`](./requirements.md) para supuestos y decisiones de producto.

## Requisitos que cubre

A1–A22. La tabla del final traza cada requisito con su componente.

## Alcance

Incluye: tabla y ciclo de vida de invitaciones, pestaña de admin, alta con validación
contra LaLiga, email normalizado, resincronización de la contraseña en el login, `/token`
simplificado y aviso de "contraseña cambiada". Fuera: ver `requirements.md §Fuera de
alcance`.

## Dependencias externas involucradas

- [x] **Login ROPC de LaLiga** (`auth/laliga_login.py`) → **VERIFICADO en esta sesión de
  forma indirecta**: la fila de credenciales del admin en producción tiene
  `ultimo_login_ok` del 2026-09-26 y ningún error. Se reutiliza tal cual, sin cambios. Sigue
  siendo una API no documentada que puede cerrarse sin aviso: por eso el pegado manual del
  token se pliega pero **no se elimina** (A18).
- [ ] **Cuentas de LaLiga creadas con Google/Apple** → **[SIN VERIFICAR]** que todas tengan
  contraseña. El admin entra con Google y su contraseña funciona; no se generaliza.
- [ ] **Bloqueo de cuenta en Azure B2C tras intentos fallidos** → **[SIN VERIFICAR]**. Si
  existe, la resincronización (D6) podría contribuir a él. Ver Riesgos.
- [ ] **Esquema y host detrás del proxy de Render** → **[SIN VERIFICAR]** que
  `request.base_url` llegue como `https://fantasy-scout.onrender.com`. uvicorn solo confía en
  `X-Forwarded-Proto` de `127.0.0.1` por defecto. Ver D8.

## Decisiones de arquitectura

### D1 — Módulos nuevos dentro de `auth/`

```
src/fantasy/auth/
├── admin.py          NUEVO: es_admin(), dependencia administrador() y NoAdministrador
├── invitaciones.py   NUEVO: crear, estado, buscar_pendiente_para_alta, revocar (lógica pura + BD)
├── alta.py           NUEVO: dar_de_alta() — orquesta invitación + LaLiga + escrituras
├── rutas.py          + GET/POST /registro, GET/POST /invitaciones, POST /invitaciones/{id}/revocar
│                       login con email normalizado y resincronización
├── credenciales_store.py / token_store.py   separar "escribir" de "commit" (D4)
└── passwords.py      + normalizar_email()
```

- **`es_admin` y `administrador` se mueven de `observabilidad/rutas.py` a `auth/admin.py`.**
  Ahora las usan dos secciones y no son observabilidad: son autorización. `observabilidad`
  pasa a importarlas de `auth`. La dependencia va de `observabilidad` → `auth`, nunca al
  revés.
- *Alternativa descartada:* un módulo `registro/` aparte. Toca las mismas tablas y la misma
  cookie que `auth/`; separarlo solo añadiría imports cruzados.

### D2 — Tabla `invitacion` y token

```
invitacion
  id           uuid pk
  token_hash   char(64) unique not null     sha256 hex del token
  creada_en    timestamptz not null default now()
  caduca_en    timestamptz not null         creada_en + 7 días
  usada_en     timestamptz null
  usuario_id   uuid null  fk user(id) on delete set null
  revocada_en  timestamptz null
```

- Token: `secrets.token_urlsafe(32)` → 256 bits. Se guarda `sha256(token)`, no Argon2: el
  token ya tiene entropía completa, así que un hash lento no aporta nada y obligaría a
  recorrer la tabla para buscar. Con SHA-256 se busca por igualdad y con índice.
- **Estado derivado, no almacenado**: `estado(invitacion, ahora)` es una función pura que
  devuelve `pendiente | usada | caducada | revocada`, con prioridad `revocada > usada >
  caducada > pendiente`. Sin columna `estado` que pueda desincronizarse.
- Caducidad: constante `CADUCIDAD_INVITACION = timedelta(days=7)` en `invitaciones.py`, sin
  variable de entorno (decisión cerrada, no hay nada que configurar).
- Retención: las invitaciones no se purgan. A esta escala son decenas de filas y sirven de
  auditoría de quién entró con qué enlace.
- *Alternativa descartada:* token firmado con `itsdangerous` sin tabla. No permite revocar,
  ni garantizar un solo uso sin ligarlo al email, ni listar el estado.

### D3 — Flujo de alta (`alta.dar_de_alta`)

```
POST /registro (invitacion, email, password)
 │
 ├─ email = normalizar_email(email)
 ├─ fila = SELECT … FROM invitacion WHERE token_hash = sha256(t) FOR UPDATE
 │     └─ no existe / no pendiente ─────────────────→ EnlaceNoValido      (A8)
 ├─ ¿existe user con ese email? ─────────────────────→ YaTieneCuenta      (A12)  sin llamar a LaLiga
 ├─ tokens = laliga_login.iniciar_sesion(email, password)
 │     ├─ CredencialesInvalidas ─────────────────────→ rollback, 400      (A10)
 │     └─ LoginNoDisponible ─────────────────────────→ rollback, 503      (A11)
 ├─ INSERT user (email, argon2(password))
 ├─ escribir credenciales cifradas + token + refresh     (sin commit, D4)
 ├─ UPDATE invitacion SET usada_en = now(), usuario_id
 └─ COMMIT  ──────────────────────────────────────────→ cookie + 302 /plantilla (A9)
```

- **El `FOR UPDATE` es lo que garantiza A13**: la segunda petición concurrente espera a que la
  primera confirme, relee la fila, la ve usada y responde `EnlaceNoValido`.
- **La llamada a LaLiga ocurre con la fila bloqueada y la transacción abierta** (hasta 20 s
  de timeout). Aceptable a esta escala: solo bloquea esa invitación concreta, y es
  precisamente lo que serializa dos usos simultáneos. *Alternativa descartada:* marcar la
  invitación como "reservada" antes de llamar a LaLiga y liberarla si falla. Es más código y
  un estado intermedio que puede quedarse colgado si el proceso muere.
- `dar_de_alta` devuelve el `Usuario` o lanza una de tres excepciones propias
  (`EnlaceNoValido`, `YaTieneCuenta`, más las dos de `laliga_login`, que se dejan pasar). La
  ruta traduce cada una en su página y código. La lógica no conoce HTTP.
- Tras el alta **no se llama a la API oficial**: el primer `/plantilla` lo hace, igual que
  hoy tras `/credenciales`.

### D4 — Escribir sin confirmar en los stores

`guardar_credenciales` y `guardar_token` hacen `commit()` dentro. Para D3 hacen falta en la
misma transacción que el `INSERT` del usuario. Se extrae de cada una la parte que escribe a
una función interna que **no confirma** (`_escribir_credenciales`, `_escribir_token`), y las
públicas quedan como `escribir + commit`. Los llamantes actuales no cambian y sus tests
siguen valiendo.

- *Alternativa descartada:* un parámetro `commit: bool = True`. Funciona, pero una bandera
  que cambia la semántica transaccional es fácil de olvidar en una llamada nueva.

### D5 — Email normalizado

`normalizar_email(e) = e.strip().lower()` en `passwords.py`, junto al resto de utilidades
de credenciales. Se aplica en `procesar_login`, en `dar_de_alta`, en `crear_usuario.py` y en
el `admin_email` (que ya lo hacía por su cuenta y pasa a usar esta función).

- Migración: antes de `UPDATE "user" SET email = lower(trim(email))` comprueba con
  `GROUP BY lower(trim(email)) HAVING count(*) > 1` y **lanza** si hay duplicados, para que
  `alembic upgrade` falle sin tocar nada. Producción hoy: 0 afectados.
- No se añade índice sobre `lower(email)`: con todas las escrituras normalizadas basta el
  `unique` existente.

### D6 — Resincronización en el login

```
procesar_login(email, password)
 ├─ usuario = buscar(normalizar_email(email))       inexistente → 401, sin LaLiga
 ├─ verificar_password(password, hash) OK           → entra (camino de hoy, sin LaLiga)
 ├─ ¿tiene credenciales guardadas?  no              → 401, sin LaLiga
 ├─ laliga_login.iniciar_sesion(email_guardado, password)
 │     ├─ acepta → actualizar hash + credenciales + tokens, limpiar ultimo_error → entra
 │     └─ rechaza / no disponible → 401 genérico, nada cambia
```

- Se usa el **email de LaLiga guardado** (descifrado), no el que teclea: puede diferir si la
  cuenta se creó con el script antiguo y `--email-laliga`.
- Una sola llamada a LaLiga por intento de login, nunca reintentos.
- El login sigue sin depender de LaLiga en el camino normal: si LaLiga está caída, quien
  pone su contraseña de siempre entra igual.

### D7 — Autorización del admin: 302 en vez de 404

`administrador()` lanza `NoAdministrador` en lugar de `HTTPException(404)`. Un
`exception_handler` en `app.py`, junto al de `NoAutenticado`, responde `302 → /plantilla`.
Aplica a `/uso` y a todas las rutas de `/invitaciones`, GET y POST. Se mantiene el fallo
cerrado: con `FANTASY_ADMIN_EMAIL` vacía nadie es admin.

- Con `hx-boost`, un 302 se sigue de forma transparente y la barra de direcciones acaba en
  `/plantilla`: es el comportamiento buscado.
- `/invitaciones` se añade a `RUTAS_IGNORADAS`, como `/uso`: las visitas del admin a sus
  pantallas no son uso real. `/registro` **sí** se registra: es el embudo de alta y aparece
  en `/uso` como cualquier ruta (sin usuario en el GET, con usuario tras el alta).

### D8 — Enlace de invitación

Se construye con `request.base_url` y, fuera de local, se fuerza el esquema `https`
(`config.es_local` ya distingue los dos casos). Así se evita una variable de entorno nueva
con la URL pública. **[SIN VERIFICAR]** el host que llega detrás del proxy de Render: se
comprueba en el ensayo (A22). Si no es el público, el plan B es `FANTASY_URL_PUBLICA` en
`render.yaml` con `value:` (no es un secreto).

### D9 — Pantallas

- **`/invitaciones`** (`invitaciones.html`): botón "Crear invitación" (form POST). Tras
  crear, la misma página muestra arriba el enlace en un `<input readonly>` y un botón
  "Copiar" con `navigator.clipboard.writeText` en un `onclick` en línea. Sin fichero `.js` ni
  build step; si el portapapeles falla, el input permite copiar a mano. Debajo, la tabla de
  invitaciones (fecha, estado, email de quien la usó, botón "Revocar" solo si pendiente).
- **`/registro`** (`registro.html`): una plantilla con dos modos, formulario o "enlace no
  válido". El token viaja en un `<input type="hidden">` del formulario, no en la acción.
  Reutiliza el texto sobre el cifrado que hoy está en `token.html`.
- **`/token`** (`token.html`): título "Tu cuenta de LaLiga"; el bloque manual pasa a
  `<details><summary>Opción avanzada</summary>…</details>`. Si las credenciales están
  rechazadas (A19), se muestra el formulario con el mensaje de contraseña cambiada en lugar
  del estado "conectada".
- **Menú**: la entrada "Token" pasa a llamarse **"Cuenta"** y se añade "Invitación" junto a
  "Uso", ambas dentro de `{% if es_admin(request) %}`.

### D10 — Aviso de "contraseña cambiada" (A19, A20)

Nueva excepción `CredencialesRechazadas(TokenInvalido)` en `official/errores.py`. En
`ClienteOficial._cabeceras`, cuando `obtener_token_valido` devuelve `None`, se consulta si
las credenciales del usuario están en estado rechazado y, si lo están, se lanza la subclase.

- Al ser subclase de `TokenInvalido`, **todo `except TokenInvalido` existente sigue
  funcionando**. Solo los tres sitios que componen el texto (`_pagina_de_jugadores`,
  `_fragmento_tabla`, `/clausulas`) eligen el mensaje según el tipo.
- El estado "rechazadas" se decide con `credenciales_rechazadas(sesion, usuario_id)` en
  `credenciales_store.py`, que compara `ultimo_error` con una **constante** compartida con el
  sitio que lo escribe. Hoy es un literal suelto; se convierte en constante para que el
  texto no pueda divergir.
- El aviso de token caducado actual ("Vuelve a pegarlo") también se reescribe sin
  tecnicismos: "No hemos podido conectar con tu cuenta de LaLiga. Revísala en Cuenta".

## Riesgos

| Riesgo | Prob. | Detección | Mitigación |
|---|---|---|---|
| El token de invitación aparece en los logs de acceso de Render (va en la query string) | Alta | Mirar logs de Render tras A22 | Aceptado: los logs solo los ves tú, el token vale una vez y caduca a los 7 días. `evento_uso` no guarda la query (O1) |
| Azure B2C bloquea una cuenta de LaLiga por intentos fallidos inducidos desde el login de fantasy-scout (D6) | Baja | Un amigo no puede entrar en la app de LaLiga | Una sola llamada por intento, sin reintentos. Límite de intentos de login queda en el backlog de huecos |
| LaLiga cierra el login ROPC | Media | A10/A11 empiezan a fallar para todos; `ultimo_error` en `/uso` | Ya no se podría dar de alta a nadie; el pegado manual (A18) sigue como respaldo para los existentes |
| Transacción abierta durante la llamada a LaLiga agota conexiones del pool | Muy baja | Errores de pool en logs | ~10 usuarios; timeout de 20 s ya acotado |
| El esquema del enlace sale `http://` detrás del proxy | Media | Enlace mostrado en A22 | D8 fuerza `https` fuera de local; plan B con variable de entorno |
| Migración aplicada en Neon después del código | Baja | La web cae al consultar `invitacion` | Regla del repo: migración antes del merge (punto de control B) |

## Trazabilidad

| Requisito | Componente |
|---|---|
| A1 | Migración + `modelos.Invitacion` (D2) |
| A2, A3 | `auth/admin.py`, handler en `app.py`, `base.html` (D1, D7, D9) |
| A4–A6 | `auth/invitaciones.py`, rutas `/invitaciones` (D2, D8, D9) |
| A7–A14 | `auth/alta.py`, rutas `/registro`, `registro.html` (D3, D4) |
| A15 | `normalizar_email`, migración (D5) |
| A16, A17 | `procesar_login` (D6) |
| A18–A20 | `token.html`, `CredencialesRechazadas`, avisos de `app.py` (D9, D10) |
| A21, A22 | Specs, README, CLAUDE.md, ensayo |

## Decisiones pendientes (preguntar al usuario)

1. ~~Nombre de la pestaña "Token" en el menú~~ → **"Cuenta"**, decidido el 2026-09-27.
2. **Plan B de D8** (`FANTASY_URL_PUBLICA`) solo si el ensayo muestra un host o esquema
   incorrecto. No requiere decisión ahora.
