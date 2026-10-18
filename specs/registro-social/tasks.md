# Tasks: Alta de cuentas de LaLiga creadas con Google u otra red social

Doce tareas en cinco bloques. Ninguna se marca hecha sin ejecutar su verificación.
Ver [`requirements.md`](./requirements.md) y [`design.md`](./design.md).

Leyenda: **[DATO]** = necesita algo que solo puedes aportar tú (o un amigo). **[PROD]** = toca
producción. **[TÚ]** = punto de control: me paro y espero tu confirmación.

Reglas de trabajo (memoria del repo): se trabaja en `feature/v0.1.0`; **commits solo al
final**, tras el end-to-end verificado, sin línea `Co-Authored-By` y con cuerpo breve; el
merge a `main` lo decides tú.

```
1. CIMIENTOS ──► 2. ALTA ──► 3. RECONEXIÓN ──► 4. ADMIN Y VIGILANCIA ──► 5. CIERRE
  T1..T4          T5, T6        T7                T8, T9                   T10..T12
```

Orden de dependencia real: el alta (T5) necesita la tabla (T1), el canje (T2), los intentos
(T3) y la escritura social en el store (T4). La reconexión (T7) reutiliza todo lo del alta.
El bloque 4 solo necesita T1 y T2, pero va después para que el e2e lo ejercite junto.

---

## Bloque 1 — Cimientos

- [x] **T1**: Migración y modelos: `laliga_credentials.tipo` (`server_default 'password'`),
  `laliga_credentials.laliga_oid`, `password_cifrado` nullable, tabla `intento_conexion`
  (D1, D2, D9). `downgrade` que se niega si hay cuentas sociales.
  - Cubre: base de S3, S4, S8.
  - Ficheros: `storage/modelos.py`, `migrations/versions/<rev>_cuentas_sociales.py`,
    `tests/test_registro_social.py` (nuevo).
  - Bloqueante: ninguno.
  - Verificación: ciclo `upgrade → downgrade → upgrade` en local; las filas existentes quedan
    `tipo='password'`; test del `downgrade` con una fila social.

- [x] **T2**: Login social en `auth/laliga_login.py`: `url_login_social`, `extraer_codigo`,
  `canjear_codigo` (con `email` y `oid` del `id_token`), `refrescar_social` y
  `sondear_vuelta_social` (D3, D10).
  - Cubre: S4 (canje), S5 (errores de la dirección), S8 (renovación), S17 (sondeo).
  - Ficheros: `auth/laliga_login.py`, `tests/test_login_social.py` (nuevo).
  - Bloqueante: ninguno.
  - Verificación, con HTTP parcheado: parámetros exactos del enlace (política, `jwt.ms`,
    `response_mode=fragment`, `nonce`, S256); `extraer_codigo` con dirección completa, solo
    fragmento, con `error` y sin `code`; canje 200 / 400 / caído; renovación con
    `scope=openid offline_access`; sondeo en sus tres resultados y el reintento.

- [x] **T3**: `auth/intentos.py`: crear un intento ligado a una invitación o a un usuario
  (borra el anterior y los caducados), buscar uno vigente por `state`, borrarlo. Verificador
  cifrado con Fernet.
  - Cubre: S3, parte de S7.
  - Ficheros: `auth/intentos.py` (nuevo), `tests/test_registro_social.py`.
  - Bloqueante: T1, T2.
  - Verificación: `code_challenge == S256(verificador)`, caducidad a 15 min, un segundo
    intento sustituye al primero, uno caducado no se encuentra, el verificador no está en
    claro en la BD.

- [x] **T4**: Store y cliente: `escribir_credenciales_social`, `estado_conexion` (sustituye a
  `credenciales_rechazadas`), rama social de `obtener_token_valido` (solo refresh, nunca
  ROPC, rotación guardada, `ERROR_RECONEXION`), `resincronizar_password` que ignora las
  sociales, `ConexionCaducada(TokenInvalido)` y su aviso en `_aviso_token`.
  - Cubre: S8, S9, S13.
  - Ficheros: `auth/credenciales_store.py`, `official/errores.py`, `official/cliente.py`,
    `api/app.py`, `tests/test_registro_social.py`.
  - Bloqueante: T1, T2.
  - Verificación: tests de la rama social (vigente, renovación OK con refresh nuevo, 400 →
    reconectar, caído → error temporal; ROPC nunca llamado); `/plantilla`, `/plantilla/tabla`
    y `/clausulas` con el aviso de S9 y código 200; login de una cuenta social con contraseña
    errónea sin llamar a LaLiga. La suite existente sigue en verde.

## Bloque 2 — Alta

- [x] **T5**: `alta.dar_de_alta_social` según D4, con sus excepciones.
  - Cubre: S4, S5 (lógica), S6.
  - Ficheros: `auth/alta.py`, `tests/test_registro_social.py`.
  - Bloqueante: T3, T4.
  - Verificación, con el canje parcheado: alta correcta (usuario, credenciales `social` con
    `oid`, token, refresh, invitación usada, intento borrado); un test por cada error de S5
    con la invitación pendiente; fallo en la última escritura sin rastro; **concurrencia
    mezclando vías** contra Postgres real, comprobada también en rojo sin el `FOR UPDATE`.

- [x] **T6**: Rutas `POST /registro/social/iniciar` y `POST /registro/social`; `registro.html`
  con las dos vías y los tres pasos; botón Pegar con su `catch`; aviso de navegador
  integrado; `rel="noopener noreferrer"`.
  - Cubre: S1, S2, S5 (páginas y códigos), S7, S18 (HTML).
  - Ficheros: `auth/rutas.py`, `api/templates/registro.html`, `tests/test_registro_social.py`.
  - Bloqueante: T5.
  - Verificación: HTML de las dos vías; el enlace lleva `noreferrer`; aviso con un
    `User-Agent` de WhatsApp y sin él con Safari, Chrome y Edge de iPhone y de ordenador;
    alta correcta deja cookie y 302; códigos de error; **S7** con `caplog` y `evento_uso`.

## Bloque 3 — Reconexión

- [x] **T7**: Rutas `POST /token/social/iniciar` y `POST /token/social`; `token.html` para
  cuentas sociales (conectada / hay que reconectar, sin el formulario de contraseña); control
  del `oid`.
  - Cubre: S10, S11.
  - Ficheros: `auth/rutas.py`, `api/templates/token.html`, `tests/test_registro_social.py`.
  - Bloqueante: T5.
  - Verificación: HTML en los dos estados; reconexión correcta limpia `ERROR_RECONEXION` y
    guarda tokens nuevos; con otro `oid` se rechaza sin escribir nada; un usuario no puede
    usar un intento de otro.

## Bloque 4 — Admin y vigilancia

- [x] **T8**: Tipo de cuenta en **Uso** (`DetalleUsuario.tipo_cuenta`) y en **Invitación**
  (vía de alta); test de que el cron incluye las cuentas sociales.
  - Cubre: S12, S14.
  - Ficheros: `observabilidad/consultas.py`, `api/templates/uso.html`,
    `api/templates/invitaciones.html`, `tests/test_registro_social.py`.
  - Bloqueante: T1.
  - Verificación: tests de las dos pantallas y de `_usuarios_con_credenciales`; una cuenta
    social con `ERROR_RECONEXION` sale en rojo en `comprobar_logins`.

- [x] **T9**: `comprobar_vuelta_social` en `vigilancia.py`, llamada desde
  `scripts/vigilancia.py`; línea fija en `/uso`.
  - Cubre: S17.
  - Ficheros: `observabilidad/vigilancia.py`, `scripts/vigilancia.py`, `api/templates/uso.html`,
    `tests/test_vigilancia.py`.
  - Bloqueante: T2.
  - Verificación: la función pura en sus tres casos; `main()` sale con 1 si está rechazada y
    con 0 si no se pudo comprobar; `/uso` no hace ninguna petición a LaLiga al cargarse.

## Bloque 5 — Cierre

- [x] **T10**: Coherencia del repo: `README.md`, `CLAUDE.md` (vía social, dependencia de
  `jwt.ms`, la vigilancia ya no es solo lectura de la BD), `specs/registro/design.md §Modelo
  de auth`.
  - Cubre: S15.
  - Bloqueante: T1–T9.
  - Verificación: revisión del diff; `uv run pytest` y `ruff --select F` en verde.

> **Cambio de plan (2026-10-12, decisión del usuario):** sin e2e con un amigo en local. T11
> queda como verificación local mía (suite, `pre_deploy`, app arrancada y pantallas con el
> canje parcheado); la prueba real con el amigo es la de T12, ya en producción.

- [x] **T11** ~~**[TÚ]** **[DATO]**~~: ~~E2E en local con un amigo de Google **a distancia**.~~ Tú
  abres `/registro` con una invitación local en tu portátil y eliges la vía social. Le mandas
  el enlace del paso 1 por WhatsApp; él entra con Google en su iPhone y te devuelve la
  dirección de `jwt.ms`. Tú la pegas, eliges una contraseña de prueba y compruebas que ves
  su plantilla. Funciona porque el login ocurre en su móvil y solo la dirección pegada llega a
  tu app. Revisión visual de `/registro` y Cuenta en móvil y escritorio, y de los errores
  (pegar algo que no es la dirección, dejar caducar el intento).
  - Cubre: S16 (parte local), S18 (parte visual).
  - Bloqueante: T10. **[DATO]** un amigo de Google disponible unos minutos.
  - Verificación: la cuenta local se crea y muestra su liga; después se borra de la BD local.

- [ ] **T12** **[PROD]** **[TÚ]**: Producción.
  1. Te pido permiso y aplico la migración en Neon (**antes del merge**).
  2. Ensayo contra Neon en el puerto 8020: login, Cuenta y Uso de tu cuenta sin cambios; la
     pantalla social se pinta. (Sin alta real aquí: la cuenta quedaría creada en producción
     sin que tu amigo la haga solo.)
  3. Commits en `feature/v0.1.0`; el merge a `main` lo haces tú o me lo pides.
  4. Tras desplegar: tu amigo se da de alta **solo desde su iPhone en Safari** con una
     invitación real; reconexión desde Cuenta en **Chrome** y en **Edge** (al menos uno en
     ordenador); **al día siguiente** sigue conectado; la vigilancia de la mañana muestra la
     comprobación de `jwt.ms` en OK.
  - Cubre: S16, S17 (en real), S18 (matriz).
  - Bloqueante: T11. **[DATO]** el amigo de Google y sus navegadores.
  - Verificación: las cuatro comprobaciones del punto 4.

---

## Descubrimientos

### T5 — El intento se borra dentro de la transacción, no con un commit propio (corrige D4)
D4 decía borrar el intento con un `commit` propio antes de canjear. Ese `commit` soltaría el
`FOR UPDATE` de la invitación antes de tiempo y abriría la puerta a dos altas con el mismo
enlace. `intentos.consumir` borra **sin confirmar**: si el canje falla, el rollback devuelve
el intento. No es un riesgo (el código de LaLiga es de un solo uso igualmente) y tiene una
ventaja: el usuario puede reabrir el mismo enlace de LaLiga sin volver al paso 1. Probado en
rojo: sin el `FOR UPDATE`, alta social + alta por contraseña con el mismo enlace crean dos
usuarios.

### T6 — El mensaje de "LaLiga rechaza tu contraseña" mandaba a un callejón sin salida
Decía "crea una contraseña desde He olvidado mi contraseña", justo lo que a los usuarios de
Google no les funciona. Ahora les dirige a la vía social de la misma página.

### T7 — Los tres pasos son una plantilla parcial (`_pasos_social.html`)
Se repetían en el alta y en Cuenta; ahora se incluyen con `{% with %}`.

### T9 — El TestClient también es un `httpx.Client`
Parchear `httpx.Client.get` para "prohibir llamadas a LaLiga" bloqueaba la propia petición
del test. Se parchea `laliga_login._sondear_una_vez`. Y `tests/test_vigilancia.py` lleva un
`autouse` que neutraliza el sondeo en `main()`: sin él, la suite llamaría a LaLiga de verdad.

### T10 — §Modelo de auth vive en `specs/mvp/design.md`, no en `specs/registro/`
S15 lo situaba mal. Se actualizó el de `specs/mvp/`.

### T11 — Verificación local con la app arrancada
El enlace que genera `/registro/social/iniciar` abre el login real de LaLiga (HTTP 200, con
Google); `noreferrer` presente; "Toca" con un User-Agent de iPhone; pegar algo que no es la
dirección → 400. `pre_deploy.sh`: solo fallan los commits y la migración de Neon (T12).

### Suite
De 314 a **393 tests**, todos en verde; `ruff --select F` limpio en lo tocado.

### T12 — Primera prueba real: "Enter token below" confunde
El amigo llegó bien a `jwt.ms`, pero la página muestra un cuadro "Enter token below" y el paso 2
decía "casi vacía": parecía que había que escribir algo ahí. El paso 2 ahora nombra ese texto y
dice explícitamente que no se escribe nada en esa página, que se copia la barra de direcciones.

### T12 — Reabrir la invitación a mitad del alta anulaba la dirección copiada
El paso 1 abre LaLiga en otra pestaña. Lo intuitivo en el móvil, tras copiar la dirección de
jwt.ms, es volver al enlace de WhatsApp; eso pulsaba otra vez "Entro con Google…" y, por S3,
creaba un intento nuevo que anulaba el anterior: la dirección copiada fallaba. Ahora
`intentos.obtener_o_crear` reutiliza el intento vigente (S3 revisado), y el paso 1 dice que
se vuelva a esta pestaña con el botón de pestañas.

### T12 — Segunda prueba real: el alta falló sin que se viera ningún mensaje
Diagnóstico con `evento_uso` en producción (solo lectura): tres `POST /registro/social` → 400
en ~20 ms (nunca llegaron a LaLiga) y dos `GET` → 405. Cinco causas, todas arregladas:
1. **Los errores eran invisibles en toda la app.** `pico-color-red-500` / `-green-500` viven en
   `pico.colors.min.css`, que nunca se cargó: los mensajes salían como texto gris normal.
   Definidas en `estilos.css` (y un test exige que toda clase de color usada esté definida).
   Además, el error se repite junto al botón "Crear mi cuenta".
2. **Safari recarga por GET una pestaña descartada** al volver a ella; la página social era la
   respuesta de un POST → 405 y formulario perdido, justo al volver de jwt.ms. Ahora la vía
   social es `GET /registro/social?invitacion=…` (y `GET /token/social`), recargable; las
   rutas antiguas `…/iniciar` redirigen con 303 en vez de dar 405.
3. **Caducidad de 15 minutos demasiado justa**: un intento caducó 5 segundos antes de pegar.
   Ahora 30.
4. **Invitaciones mezcladas** (se crearon cinco en media hora): pegar la dirección de otra
   invitación decía "caducado". Ahora `IntentoDeOtraInvitacion` lo dice claro.
5. **Sin rastro del motivo**: ahora cada rechazo deja "alta social rechazada: <tipo>" en el
   log de Render (solo el tipo, nunca lo pegado).

### T12 — La vía social no se veía al abrir la invitación
Estaba al final de `/registro`, como botón secundario bajo el formulario de contraseña, y el
`autofocus` del email abría el teclado en el móvil y lo tapaba todo. Ahora la página empieza
por "¿Cómo entras en LaLiga Fantasy?" con dos botones al mismo nivel (Google/Apple/Facebook
primero); el formulario de contraseña sigue debajo, sin `autofocus`.

### T12 — Falló en Chrome y funcionó en Safari (causa sin confirmar)
Sospechosos: la recarga de pestaña por GET (arreglada en `d9ba016`) o, en Android, que la app
de LaLiga abra `login.laliga.es` en lugar del navegador. Con los errores ya visibles y el
motivo en el log, la próxima prueba en Chrome lo dirá.
