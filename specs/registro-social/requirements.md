# Requirements: Alta de cuentas de LaLiga creadas con Google u otra red social

Objetivo del feature: **que un amigo cuya cuenta de LaLiga Fantasy es de Google (o Apple,
Facebook…) se dé de alta en fantasy-scout desde su iPhone, solo, con la misma invitación**, y
que su cuenta se mantenga conectada sin contraseña de LaLiga.

Numeración `S#`. Se apoya en `specs/registro/` (invitaciones, alta, admin) sin cambiar su
comportamiento. La investigación que lo hace posible está en
`specs/registro/tasks.md §Investigación` y en `specs/registro/spike_google.py`.

---

## Cómo funciona (resumen para revisar los requisitos)

1. fantasy-scout genera un enlace al **login oficial de LaLiga** (código de autorización +
   PKCE, política `B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN`, vuelta a `https://jwt.ms`).
2. El amigo entra con Google; Safari acaba en `https://jwt.ms/#code=…`.
3. Copia esa dirección, vuelve a fantasy-scout y la pega.
4. fantasy-scout canjea el código por un token y un **refresh token** (90 días, rota en cada
   renovación), lee el email del token y crea la cuenta con una contraseña propia.
5. El cron renueva su sesión a diario con el refresh token.

---

## Supuestos de entrada (corrígeme ahora)

1. **La misma invitación sirve para las dos vías.** `/registro` ofrece "con contraseña de
   LaLiga" (lo de hoy) y "con Google u otra red social". No hay invitaciones de un tipo u otro.
2. **El email lo pone LaLiga, no el usuario.** Sale del token (`email`), que LaLiga ya ha
   verificado. El usuario no lo escribe.
3. **El intento de conexión caduca a los 30 minutos** (eran 15; ver T12). El verificador PKCE se guarda en el
   servidor ligado a la invitación; si tarda más, empieza otra vez desde fantasy-scout.
4. **Todo puede ocurrir en el mismo móvil**, en dos pestañas del navegador: la de
   fantasy-scout y la del login de LaLiga. No hace falta ordenador, pero también funciona en
   uno (S18).
5. **La contraseña propia** sigue las mismas reglas que cualquier otra: mínimo 8 caracteres,
   escrita dos veces. Se guarda con Argon2, como todas.
6. **Spec fuera de git**, como las demás.

---

## Decisiones cerradas (2026-10-12)

| Decisión | Elegido | Motivo |
|---|---|---|
| Vía técnica | Código de autorización + PKCE con vuelta a `https://jwt.ms` | Única probada que funciona en un iPhone sin ordenador (ver investigación) |
| Cómo entra después en la app | **Contraseña propia de fantasy-scout**, elegida en el alta | Pedir otra vez Google cada 30 días sería pesado |
| Redes sociales | **Google y las demás** que ofrece LaLiga | Mismo flujo, coste cero; solo Google está probado |
| Dónde vive la spec | `specs/registro-social/` | Capacidad aparte de la de `specs/registro/` |
| Navegadores | **Safari, Chrome y Edge**, en iPhone y en ordenador (2026-10-12) | Petición del usuario: sus amigos no usan todos Safari |
| Vigilar que LaLiga mantiene `jwt.ms` | **Sí, en la vigilancia de cada mañana** (2026-10-12) | Enterarse el mismo día, no cuando un amigo no pueda darse de alta |

---

## Bloque A — Alta

- [ ] **S1**: Con una invitación pendiente, `/registro` ofrece las dos vías: el formulario de
  email y contraseña de LaLiga (sin cambios) y **"Entro en LaLiga con Google, Apple u otra
  red social"**.
  - Verificación: test del HTML con invitación válida; con invitación no válida sigue sin
    haber ningún formulario (A8 de `specs/registro`).
- [ ] **S2**: Al elegir la vía social, la página guía en tres pasos, en lenguaje llano:
  (1) botón **"Abrir el login de LaLiga"**, que abre el enlace en otra pestaña;
  (2) qué hacer al acabar en la página de Microsoft: copiar la dirección completa;
  (3) campo para pegarla, con un botón **"Pegar"** que lee el portapapeles, más la contraseña
  para fantasy-scout escrita dos veces.
  - Verificación: test del HTML; revisión visual en iPhone en el e2e (S16).
- [ ] **S3**: El enlace del paso 1 lleva un verificador PKCE y un `state` nuevos, guardados en
  el servidor ligados a esa invitación, que caducan a los **30 minutos** (eran 15; ver T12). Pedir la vía social
  otra vez **reutiliza el intento vigente** (revisado tras la primera prueba real: reabrir la
  invitación de WhatsApp no debe anular la dirección ya copiada); solo si caducó se crea otro.
  - Verificación: test que comprueba `code_challenge = S256(verificador)`, el `state`, la
    caducidad y que un segundo intento sustituye al primero.
- [ ] **S4**: Alta correcta: se acepta la dirección completa (`https://jwt.ms/#state=…&code=…`)
  o solo su parte tras `#`. Si el `state` coincide y LaLiga canjea el código, **en una sola
  transacción** se crea el usuario (email del token, normalizado; hash Argon2 de su
  contraseña propia), se guardan cifrados el token y el refresh token, y la invitación queda
  usada. Se abre la sesión y se responde **302 a `/plantilla`**.
  - Verificación: test con el canje parcheado; mismas comprobaciones que A9.
- [ ] **S5**: Errores, todos con el formulario aún disponible y **la invitación pendiente**:
  - lo pegado no contiene `code` → "Eso no parece la dirección de la página de Microsoft";
  - LaLiga devolvió `error` en la dirección → su mensaje traducido a lenguaje llano;
  - `state` distinto o intento caducado → "Este intento ha caducado, vuelve al paso 1";
  - LaLiga rechaza el canje (código caducado o ya usado) → "vuelve al paso 1";
  - LaLiga no responde → 503, "inténtalo en un rato";
  - el email del token ya tiene cuenta → mensaje con enlace a `/login` (como A12);
  - el token no trae email → "escribe a quien te invitó";
  - contraseñas distintas o de menos de 8 caracteres → se dice cuál.
  - Verificación: un test por caso.
- [ ] **S6**: Dos altas simultáneas con la misma invitación crean un solo usuario, también
  mezclando las dos vías.
  - Verificación: test de concurrencia como A13.
- [ ] **S7**: Ni el verificador, ni el código pegado, ni los tokens, ni la contraseña aparecen
  en logs, en `evento_uso` ni en respuestas HTML. El `state` sí puede viajar en el HTML (es
  público por diseño).
  - Verificación: test con `caplog` y `evento_uso` como A14.

## Bloque B — Mantener la conexión

- [ ] **S8**: Para una cuenta social, la renovación usa **solo** el refresh token y guarda el
  nuevo en cada renovación (rota). **Nunca** se intenta el login con contraseña de LaLiga.
  - Verificación: tests de `obtener_token_valido` para una cuenta social: token vigente,
    renovación correcta (refresh nuevo guardado) y renovación fallida (sin llamar a ROPC).
- [ ] **S9**: Si la renovación falla porque LaLiga rechaza el refresh token (caducado o
  revocado), la cuenta queda en estado **"hay que reconectar"**. Las páginas de datos avisan
  con 200: **"Tu conexión con LaLiga ha caducado: vuelve a conectarla"**, con enlace a Cuenta.
  Si LaLiga simplemente no responde, se trata como hoy (aviso de error temporal).
  - Verificación: tests de `/plantilla`, `/plantilla/tabla` y `/clausulas` en ese estado.
- [ ] **S10**: **Cuenta** (`/token`) muestra a una cuenta social su estado ("conectada con
  Google u otra red social") y, si hay que reconectar, el mismo flujo de tres pasos sin la
  contraseña. Al reconectar se actualizan token y refresh token y se limpia el estado.
  - Verificación: tests del HTML en los dos estados y del POST de reconexión.
- [ ] **S11**: La reconexión solo se acepta si es **la misma cuenta de LaLiga** que se dio de
  alta (mismo identificador de usuario de LaLiga en el token). Si es otra, se rechaza sin
  tocar nada.
  - Verificación: test con un token de otro usuario.
- [ ] **S12**: El cron incluye las cuentas sociales entre las que pueden renovar y leer el
  mercado, igual que las de contraseña.
  - Verificación: test de `_usuarios_con_credenciales` en `snapshot_diario.py`.

## Bloque C — Login y admin

- [ ] **S13**: Una cuenta social entra en `/login` con su email y su contraseña propia. La
  resincronización con LaLiga (A16) **no se intenta nunca** para ella.
  - Verificación: test de login correcto y de contraseña errónea sin llamada a LaLiga.
- [ ] **S14**: En **Uso** y en **Invitación** se ve el tipo de cada cuenta ("contraseña de
  LaLiga" o "red social"), y las cuentas sociales que necesitan reconectar cuentan en el
  estado de conexiones de Uso y en la vigilancia diaria como las demás.
  - Verificación: tests de las dos pantallas y de la comprobación de vigilancia.

- [ ] **S17**: La **vigilancia de cada mañana** comprueba que LaLiga sigue aceptando
  `https://jwt.ms` como dirección de vuelta, con una sola petición `GET` a su página de login,
  sin credenciales:
  - aceptada → OK;
  - rechazada (`AADB2C90006`) → **ALERTA**: el workflow queda en rojo y llega el email;
  - no se pudo comprobar (red, respuesta rara) tras un reintento → se dice en el informe,
    **sin alarma** (regla de la vigilancia: datos insuficientes no es alarma).
  El panel `/uso` **no** hace esta petición al cargarse: indica que se comprueba en el email
  de la mañana.
  - Verificación: tests de la función pura en los tres casos y del sondeo con HTTP parcheado;
    `scripts/vigilancia.py` sale con 1 en el caso rechazada.
- [ ] **S18**: El alta social y la reconexión funcionan en **Safari, Chrome y Edge**, en
  **iPhone y en ordenador**:
  - las instrucciones del paso 2 valen para cualquiera de ellos ("toca" en móvil, "haz clic"
    en ordenador, sin depender de un navegador concreto);
  - el botón **Pegar** usa el portapapeles cuando el navegador lo permite, y **pegar a mano
    en el campo funciona siempre** (si el botón falla, lo dice y no rompe nada);
  - si la página se abre dentro de WhatsApp, Instagram o Facebook, se avisa de abrirla en el
    navegador (D7 del diseño).
  - Verificación: tests del HTML (sin dependencias de un navegador concreto); matriz manual
    en S16.

## Bloque D — Coherencia y verificación real

- [ ] **S15**: `README.md`, `CLAUDE.md` y `specs/registro/design.md §Modelo de auth`
  describen la vía social y su dependencia de `https://jwt.ms`. `CLAUDE.md` deja de decir que
  la vigilancia "solo lee la BD".
  - Verificación: revisión del diff; `uv run pytest` en verde.
- [ ] **S16**: E2E en producción: un amigo con cuenta de Google se da de alta **desde su
  iPhone, solo**, con una invitación real, y ve su plantilla. Al día siguiente, tras el cron,
  sigue conectado sin hacer nada.
  Matriz de navegadores (S18), sin necesitar más cuentas de Google: el **alta** en Safari
  (iPhone) y la **reconexión** desde Cuenta, con la misma cuenta, en Chrome y en Edge (al
  menos uno en ordenador).
  - Verificación: manual, con el usuario; el "día siguiente" confirma la renovación real.

---

## Dependencias aún no disponibles

- **Tope total de sesión** desde el primer login (ventana deslizante de B2C): sin verificar.
  Si existe, S9/S10 lo cubren: el usuario reconecta cada ~90 días. Se sabrá con el tiempo.
- **`https://jwt.ms` registrado en LaLiga**: puede desaparecer sin aviso. Si pasa, las altas
  sociales fallan con un error claro y las cuentas ya creadas siguen mientras viva su
  refresh token.
- **Apple, Facebook y demás**: sin probar. El primer amigo que los use es la prueba.

## Fuera de alcance (explícito)

- Entrar en fantasy-scout con Google en vez de con la contraseña propia.
- Recuperar la contraseña propia olvidada (la puerta trasera sigue siendo el admin).
- Convertir una cuenta existente de un tipo a otro.
- Sustituir `jwt.ms` por una dirección propia (LaLiga no la tiene registrada).
- Varias ligas por usuario.
