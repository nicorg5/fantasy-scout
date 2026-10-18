# Design: Alta de cuentas de LaLiga creadas con Google u otra red social

## Contexto

Las cuentas de LaLiga creadas con Google no tienen contraseña: el login ROPC no les sirve.
La investigación (`specs/registro/tasks.md §Investigación`) probó con un amigo real que el
flujo **código de autorización + PKCE** con vuelta a `https://jwt.ms` funciona desde un
iPhone y da un refresh token de 90 días que rota. Ver
[`requirements.md`](./requirements.md).

## Requisitos que cubre

S1–S16. Trazabilidad al final.

## Dependencias externas involucradas

- [x] **Política `B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN` con PKCE, Google y vuelta a
  `https://jwt.ms`** → **VERIFICADO** el 2026-10-12 con una cuenta real de Google
  (`specs/registro/spike_google.py`): canje, `email` en el token, API con 200, renovación y
  rotación del refresh token.
- [x] **`nonce` obligatorio** y `scope=openid offline_access` → **VERIFICADO**.
- [ ] **Renovación con el `scope` que usa hoy `laliga_login.refrescar`**
  (`openid <client_id> offline_access`) para tokens emitidos con `openid offline_access` →
  **[SIN VERIFICAR]**. Lo verificado es renovar con el **mismo** scope del canje, y eso es lo
  que se hará (D3).
- [ ] **Tope total de sesión** (ventana deslizante de B2C) → **[SIN VERIFICAR]**; cubierto por
  la reconexión (D5).
- [ ] **`https://jwt.ms` sigue registrado** → **[SIN VERIFICAR a futuro]**: LaLiga puede
  quitarlo sin aviso. Ver Riesgos.
- [ ] **Apple, Facebook y demás** → **[SIN VERIFICAR]**: mismo flujo, sin probar.
- [ ] **Google bloquea su login dentro de navegadores integrados** (WhatsApp, Instagram,
  Facebook: error `disallowed_useragent`) → **[SIN VERIFICAR para este caso]**, pero es una
  política documentada de Google. Ver D7.

## Decisiones de arquitectura

### D1 — La cuenta social vive en la misma tabla de credenciales

`laliga_credentials` gana dos columnas y relaja una:

```
tipo            varchar(16) not null default 'password'   'password' | 'social'
laliga_oid      varchar(64) null      identificador del usuario en LaLiga (claim `oid`)
password_cifrado                    pasa a NULLABLE (una cuenta social no tiene)
```

- **Por qué la misma tabla:** el cron (`_usuarios_con_credenciales`), Uso
  (`estado_tokens`) y la vigilancia (`comprobar_logins`, que mira `ultimo_error`) ya leen de
  ella. Una cuenta social entra en los tres **sin tocarlos** (S12, S14). `email_cifrado`
  guarda el email del token; `refresh_token_cifrado` ya existe.
- `laliga_oid` va **en claro**: es un identificador, no un secreto, y hay que compararlo en la
  reconexión (S11). Se rellena también en las cuentas de contraseña a partir de su próximo
  login completo, aunque hoy nadie lo usa para ellas.
- *Alternativa descartada:* una tabla `conexion_social` aparte. Habría que enseñar a las tres
  consultas anteriores a leer de dos sitios.

### D2 — Intento de conexión en el servidor, buscado por `state`

```
intento_conexion
  id                  uuid pk
  state               varchar(64) unique not null
  verificador_cifrado bytea not null           Fernet, como el resto de secretos
  invitacion_id       uuid null fk invitacion on delete cascade   (alta)
  usuario_id          uuid null fk user on delete cascade         (reconexión)
  caduca_en           timestamptz not null     creado + 15 min
```

- El `state` viaja en la dirección que se pega (`#state=…&code=…`), así que el intento se
  encuentra **aunque el usuario pegue en otro navegador** del que abrió. Una cookie fallaría en
  ese caso, y con gente poco técnica pasará.
- El verificador se cifra: durante 15 minutos es media credencial.
- Al crear un intento se borran el anterior de esa invitación o usuario (S3) y los caducados
  de cualquiera, que es toda la limpieza que hace falta.
- Se borra al usarse, salga bien o mal el canje: el código de LaLiga es de un solo uso.

### D3 — Login social en `auth/laliga_login.py`

Junto al login ROPC, tres funciones nuevas, mismas reglas (credenciales nunca en logs):

- `url_login_social(state, reto) -> str`: la política interactiva, `redirect_uri=https://jwt.ms`,
  `response_mode=fragment`, `scope=openid offline_access`, `nonce=state`, PKCE S256.
- `canjear_codigo(code, verificador) -> TokensSocial`: canje en el endpoint `token`; devuelve
  `TokensLaLiga` más `email` y `oid` leídos del `id_token`.
- `refrescar_social(refresh) -> TokensLaLiga`: como `refrescar`, pero con
  `scope=openid offline_access`, que es lo verificado.

El `id_token` **no se valida criptográficamente**: llega directamente del endpoint `token`
de LaLiga por TLS, en respuesta a nuestro propio canje con PKCE. OpenID Connect permite omitir
la validación de la firma en ese caso, y es lo que ya hacemos con ROPC.

`extraer_codigo(pegado) -> (state, code)` acepta la dirección entera o solo lo que va tras
`#` (o tras `?`). Si trae `error`, lanza `ErrorEnLaDireccion` con el `error_description` de
LaLiga, que la ruta traduce a lenguaje llano.

### D4 — Alta social (`auth/alta.py`)

```
dar_de_alta_social(sesion, token_invitacion, pegado, password, password2)
 ├─ validar contraseñas (≥ 8, iguales)  ────────────→ ContrasenaNoValida   (antes de nada)
 ├─ state, code = extraer_codigo(pegado)  ──────────→ DireccionNoValida / ErrorEnLaDireccion
 ├─ invitacion = buscar_pendiente(..., bloquear=True) → EnlaceNoValido
 ├─ intento = por state; de esta invitación; vigente → IntentoNoValido
 ├─ borrar intento (de un solo uso, pase lo que pase)
 ├─ tokens = canjear_codigo(code, verificador)  ────→ CredencialesInvalidas → IntentoNoValido
 │                                                  LoginNoDisponible → 503
 ├─ sin email en el token ──────────────────────────→ SinEmail
 ├─ email ya tiene cuenta ──────────────────────────→ YaTieneCuenta
 ├─ INSERT user (email normalizado, argon2(password))
 ├─ credenciales tipo=social, email, oid, refresh; token
 ├─ marcar invitación usada
 └─ COMMIT
```

- **Mismo patrón que D3 de `specs/registro`**: `FOR UPDATE` sobre la invitación, que
  serializa también una alta por contraseña simultánea con la misma invitación (S6).
- **El email se conoce después de canjear**, al revés que en la vía de contraseña. Si ya tiene
  cuenta, el código se ha gastado; el usuario tiene una cuenta y no necesita otro.
- El borrado del intento se confirma aunque falle el resto: se hace en un `commit` propio
  antes de canjear. Un intento a medias no debe poder reutilizarse.

### D5 — Renovación y reconexión

En `credenciales_store.obtener_token_valido`, tras el token guardado:

```
tipo == 'social':
  refrescar_social(refresh)
    ├─ OK → guardar token y refresh nuevo (rota), limpiar ultimo_error
    ├─ CredencialesInvalidas (400: caducado o revocado) → ultimo_error = ERROR_RECONEXION; None
    └─ LoginNoDisponible → ultimo_error = "login no disponible"; None   (temporal)
  nunca ROPC
tipo == 'password': lo de hoy (refresh → ROPC)
```

- `ERROR_RECONEXION` es una constante, como `ERROR_CREDENCIALES_RECHAZADAS` (D10 de
  `specs/registro`).
- `credenciales_rechazadas()` se generaliza en `estado_conexion(sesion, usuario_id)`, que
  devuelve `CONECTADA | CONTRASENA_CAMBIADA | RECONECTAR | SIN_CREDENCIALES`.
- `ClienteOficial._cabeceras` lanza una nueva `ConexionCaducada(TokenInvalido)` en el caso
  `RECONECTAR`, y `_aviso_token` le pone el texto de S9. Como es subclase de `TokenInvalido`,
  ningún `except` existente cambia.
- **Reconexión** (S10, S11): mismo flujo de tres pasos desde Cuenta, con el intento ligado al
  `usuario_id`. Tras el canje se compara el `oid` del token con `laliga_oid`; si difieren, se
  rechaza sin escribir nada.
- `resincronizar_password` (A16) devuelve `False` de inmediato para una cuenta social (S13).

### D6 — Rutas

| Ruta | Para qué |
|---|---|
| `POST /registro/social/iniciar` | crea el intento y pinta los tres pasos con el enlace (S2, S3) |
| `POST /registro/social` | `invitacion`, `pegado`, `password`, `password2` → alta (S4, S5) |
| `POST /token/social/iniciar` | reconexión: intento ligado al usuario (S10) |
| `POST /token/social` | `pegado` → reconexión (S10, S11) |

`/registro` y `/token` ya existen; solo cambian sus plantillas. Las nuevas son POST para que
un `GET` no cree intentos. El middleware de uso las registra solo, y `evento_uso` no guarda
el cuerpo (S7).

### D7 — Pantalla de la vía social

- **Paso 1**: `<a href="…" target="_blank" rel="noopener noreferrer">`. **`noreferrer` es
  obligatorio**: la página de alta lleva el token de la invitación en su URL y, sin él, Safari
  se lo mandaría a LaLiga como `Referer`.
- **Paso 2**: texto llano con lo que va a ver ("una página de Microsoft casi vacía") y cómo
  copiar la dirección en el iPhone.
- **Paso 3**: `<textarea>` más un botón **Pegar** con `navigator.clipboard.readText()` en un
  `onclick` en línea (Safari muestra su burbuja "Pegar"; si falla, se pega a mano). Debajo, las
  dos contraseñas. Sin fichero `.js`.
- **Navegadores (S18)**: nada depende de Safari. En iPhone, Chrome y Edge usan el mismo motor
  que Safari (WebKit), así que se comportan igual con `jwt.ms` y con el portapapeles; en
  ordenador, Chrome y Edge piden permiso para leer el portapapeles la primera vez. El botón
  Pegar envuelve `readText()` en un `catch` que muestra "pega a mano con mantener pulsado o
  Ctrl+V". El texto del paso 2 detecta móvil u ordenador solo para decir "toca" o "haz clic".
- **Navegador integrado**: si el `User-Agent` es de WhatsApp, Instagram o Facebook, se avisa
  arriba: "Abre esta página en Safari: Google no deja entrar desde aquí". Es solo un aviso,
  nunca un bloqueo: la detección por `User-Agent` es aproximada.
- La invitación de WhatsApp sigue llevando a `/registro`, que elige entre las dos vías (S1).

### D8 — Admin

- **Uso**: `DetalleUsuario` gana `tipo_cuenta` (`contraseña` / `red social` / `sin conexión`),
  leído de `laliga_credentials.tipo`.
- **Invitación**: la columna "Usada por" añade la vía con la que se dio de alta.
- La vigilancia y "Conexiones con LaLiga" de Uso no cambian (D1).

### D10 — Vigilar que `jwt.ms` sigue aceptada (S17)

- `laliga_login.sondear_vuelta_social() -> SondeoVuelta` hace un `GET` a la página de login
  con `redirect_uri=https://jwt.ms` (sin credenciales, sin seguir redirecciones) y devuelve
  `ACEPTADA` si responde 200 con la página de login, `RECHAZADA` si aparece `AADB2C90006`, o
  `NO_COMPROBADA` si hay error de red o una respuesta que no encaja. Reintenta una vez a los
  30 s antes de decir `NO_COMPROBADA`.
- `vigilancia.comprobar_vuelta_social(sondeo) -> Comprobacion` es pura, como las demás:
  `RECHAZADA` → alerta; `NO_COMPROBADA` → OK con el motivo en el detalle.
- **Se llama desde `scripts/vigilancia.py`, no desde `evaluar()`**: `evaluar()` también la
  usa el panel `/uso` en cada carga, y el panel no debe llamar a LaLiga cada vez que se abre.
  `/uso` muestra en su lugar una línea fija: "Dirección de vuelta de Google: se comprueba en
  el email de la mañana". Es la única comprobación que el panel no repite; se acepta porque
  depende de un tercero, no de nuestra BD.
- La vigilancia deja de ser "solo lectura de la BD": hace esa petición externa. Sigue sin
  necesitar la clave de cifrado ni credenciales.

### D9 — Migración

Una revisión: dos columnas en `laliga_credentials` (`tipo` con `server_default 'password'`,
`laliga_oid`), `password_cifrado` a nullable y la tabla `intento_conexion`. Las filas
existentes quedan como `password`. El `downgrade` falla si hay cuentas sociales, porque no se
pueden convertir en filas con contraseña obligatoria.

## Riesgos

| Riesgo | Prob. | Detección | Mitigación |
|---|---|---|---|
| LaLiga quita `https://jwt.ms` de su registro | Media | El alta social falla en el paso 1 con `AADB2C90006` | Error claro al usuario; las cuentas ya creadas siguen mientras viva su refresh token. No hay alternativa conocida en iPhone |
| Tope total de sesión de 90 días | Media | `ultimo_error = ERROR_RECONEXION` en Uso y en la vigilancia | Reconexión desde Cuenta en un minuto (D5) |
| Abren la invitación en el navegador de WhatsApp y Google bloquea el login | Alta | Error de Google en el paso 1 | Aviso de D7 e instrucciones en el mensaje de invitación |
| Pegan en otro navegador | Media | — | El intento se busca por `state` (D2): funciona igual |
| Tardan más de 15 minutos o reutilizan una dirección vieja | Media | `IntentoNoValido` | Mensaje "vuelve al paso 1"; el paso 1 crea otro intento |
| Renovar con otro `scope` del de emisión falla | Baja | Tests no lo cubren | Se usa el scope verificado (D3); el e2e de "al día siguiente" (S16) lo confirma en real |
| El token de invitación llega a LaLiga por `Referer` | Alta sin mitigar | — | `rel="noreferrer"` (D7) y test del atributo |
| La comprobación de `jwt.ms` da falsas alarmas por un fallo de red | Baja | Email de vigilancia | Un reintento y, si sigue, `NO_COMPROBADA` sin alarma (D10) |

## Trazabilidad

| Requisito | Componente |
|---|---|
| S1, S2 | `registro.html`, D6, D7 |
| S3 | `intento_conexion`, D2 |
| S4–S6 | `alta.dar_de_alta_social`, D4 |
| S7 | D2 (cifrado), D6 (POST), tests de fuga |
| S8, S9 | `obtener_token_valido`, `ConexionCaducada`, D5 |
| S10, S11 | `token.html`, rutas `/token/social*`, D5 |
| S12 | D1 (sin cambios en el cron) |
| S13 | `resincronizar_password`, D5 |
| S14 | D8 |
| S17 | D10 |
| S18 | D7 |
| S15, S16 | Docs, e2e |

## Decisiones pendientes (preguntar al usuario)

Ninguna. Las de producto se cerraron en `requirements.md`.
