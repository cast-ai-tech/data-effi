# effi-capture — qué es esto y cómo se usa

> **Para Alexander y para quien mantenga esto después.** Lo que se le envía a la
> persona que tiene la cuenta de Effi es [INSTRUCCIONES.md](INSTRUCCIONES.md);
> este archivo es el de adentro.

## El problema que resuelve

Effi no publica API. Para que Master Data pueda entrar con la cuenta de cada
comerciante hacen falta seis datos que solo se ven mirando el tráfico del
navegador: la ruta del formulario de entrar, cómo se llaman los campos de usuario
y contraseña, si hay un token CSRF, si la sesión vuelve como cookie o como JSON, y
cómo se llama esa cookie.

Esos seis datos se pueden sacar de un HAR. Y ahí aparece el problema real: **un
HAR contiene la contraseña que se tecleó, en texto plano.** «Mándame el HAR» es
«mándame tu contraseña por WhatsApp», y eso no se le pide a un cliente.

Esta carpeta existe para que la persona pueda ayudarnos **sin mandarnos nunca un
secreto**.

## Las dos vías

| | Extensión de Chrome | `analizador.html` |
|---|---|---|
| Instala algo | sí, en modo desarrollador | no, es un archivo que se abre |
| Hay que abrir DevTools | no | sí |
| Repetible sin equivocarse | sí, dos botones | no tanto: hay que acordarse de *Preserve log* |
| Funciona en Firefox | no | sí |
| Genera un archivo con la contraseña | **no, nunca** | sí (el `.har`, que no se envía) |

La extensión es la vía principal justamente por la fila «repetible»: el flujo del
HAR se hace mal a la primera casi siempre — hay que abrir DevTools *antes* de
entrar, marcar *Preserve log* *antes* de que la página navegue, y no cerrar el
panel — y quien lo hace solo se entera de que salió vacío cuando ya cerró sesión.

## Qué se captura, y qué no

**Se guarda:** la ruta, el método, los *nombres* de los campos, el HTTP de vuelta,
el *nombre* de la cookie de sesión, el tipo de contenido.

**No se toca:** el valor de ningún campo, cookie, encabezado ni cuerpo de
respuesta.

`chrome.webRequest` entrega el formulario entero en `requestBody.formData`, o sea
que **la contraseña pasa literalmente por `background.js` en cada captura**. Lo
único que se hace con ese objeto es `Object.keys(...)`. Ese es el archivo entero,
y es la razón de que el resultado se pueda pegar en un chat sin pensarlo.

## Los archivos

```
tools/effi-capture/
├── README.md              este archivo (interno)
├── INSTRUCCIONES.md       lo que se le envía a la persona
├── analizador.html        plan B: lee un .har sin instalar nada
└── extension/
    ├── manifest.json      permisos: solo webRequest + storage, solo Effi
    ├── background.js      la captura, con la regla de arriba
    ├── contrato.js        la lógica compartida - NUNCA lee valores
    ├── config.js          a dónde enviar; lo escribe el empaquetador
    ├── popup.html         la ventanita
    └── popup.js           tres botones y un veredicto
```

`contrato.js` lo usan las dos vías a propósito. Si cada una tuviera su lógica,
acabarían dando respuestas distintas sobre el mismo Effi, y esa discrepancia
cuesta un día averiguarla. Hay un tercer espejo en Python
(`scripts/extract_effi_contract.py`) para analizar un `.har` desde la terminal:
**si cambias las pistas de nombres, cámbialas en los dos sitios.**

## Cómo enviarlo

**Con envío automático** (recomendado). Genera un código de invitación desde la
app o con `POST /captures/tokens?label=Juan, de Distrilatam`, y empaqueta con la
`submit_url` que devuelve:

```bash
python -m scripts.empaquetar_captura --url "https://api.tudominio.com/captures/CODIGO"
```

`--url` hace dos cosas que van juntas siempre: escribe `extension/config.js` y
añade el host a `host_permissions` del manifest. Solo lo primero y Chrome bloquea
el envío por CORS — la extensión falla en el último paso, en el computador de otra
persona, sin que nadie sepa por qué. Por eso lo hace el script y no una persona.

**Sin `--url`** el paquete sigue sirviendo: la extensión enseña el texto y quien
capturó lo copia. Es el modo manual, y es un final válido — el botón de enviar
simplemente no aparece.

El código caduca a los 14 días y admite 10 usos, así que un `.zip` olvidado en la
carpeta de descargas de alguien deja de servir solo.

## Cuando la captura llega

Con envío automático llega sola: aparece en `GET /captures` y **suena la campana**
con una notificación que dice si vino completa o si le faltó el login. Quien
capturó ve en el momento un mensaje escrito para él, así que si le faltó algo
puede repetir con Effi todavía abierto — en vez de enterarse mañana.

Lo que **no** pasa, a propósito: recibir una captura **no configura nada**. Se
guarda y se avisa a un humano. Que un POST de fuera pudiera cambiar por sí solo
cómo entramos a la cuenta de un comerciante sería justo el agujero que este
diseño evita, y hay un test que lo vigila
(`test_una_captura_no_se_aplica_sola`).

## Qué hacer con lo que llegue

El contrato trae esto:

```
EFFI_BASE_URL=https://effi.com.co
EFFI_LOGIN_PATH=/ingreso/validar_usuario
EFFI_LOGIN_USER_FIELD=email
EFFI_LOGIN_PASS_FIELD=password
EFFI_LOGIN_CSRF_FIELD=token
EFFI_SESSION_CARRIER=cookie
EFFI_SESSION_COOKIE=ci_session
```

Esos valores no son de ejemplo: son los que se leen en el formulario de
`https://effi.com.co/ingreso` a 2026-08-26. El Effi real es CodeIgniter — de ahí
la cookie `ci_session` y el CSRF llamado `token`, que se emite en la propia
página de entrada y va atado a esa cookie. El formulario manda además
`email_no_verificado` y `password_encrypt`, los dos vacíos: ningún JavaScript de
la página los rellena.

Ojo con `app.effi.com.co`, que aparece como `DEFAULT_BASE_URL` en
`connectors/effi/auth.py` y en `session_fetcher.py`: ese host **no existe**, no
resuelve en DNS. El bueno es el dominio pelado.

## El muro: el login lleva reCAPTCHA

El botón «Ingresar» de `/ingreso` es un reCAPTCHA v2 invisible (`class="g-recaptcha"`,
`data-sitekey`, `data-callback="onSubmit"`), y el envío solo ocurre dentro del
callback que Google invoca al resolverlo. Es decir: el POST a
`/ingreso/validar_usuario` viaja con un `g-recaptcha-response` que **solo Google
emite**, y un cliente nuestro no puede fabricar.

Eso no lo arregla una captura mejor. Mientras siga ahí, entrar a Effi con usuario
y contraseña desde el servidor no es un problema de contrato sino de diseño, y
hay que resolverlo por otro lado: pedirle a Effi una API con token, o que el
comerciante entregue la sesión ya hecha. Antes de tocar
`LOGIN_CONTRACT_VERIFIED` conviene decidir eso.

1. Pega esas líneas en el `.env` del servidor (y en Render).
2. Compara las **descargas de reportes** que trae la captura con
   `PERMISSION_PROBES` en `connectors/effi/permissions.py`. Ahora mismo solo dos
   rutas vienen de código real; las demás son suposiciones y hay que corregirlas
   con lo que diga la captura.
3. Pon `LOGIN_CONTRACT_VERIFIED = True` en `connectors/effi/auth.py`.
4. Borra el test `test_login_refuses_to_run_while_the_contract_is_unverified`,
   que existe precisamente para fallar en este momento.
5. Prueba contra una cuenta real con **Probar conexión** en la pantalla de
   conexiones, antes de dárselo a nadie.

## Si Effi usa otro dominio

`manifest.json` vigila `*.effi.com.co` y `*.efficommerce.com`. Si la persona
reporta «0 peticiones», casi seguro es que su Effi vive en otra dirección. Añádela
a `host_permissions` **y a los dos `{ urls: [...] }` de `background.js`** — los
tres tienen que coincidir o la extensión pedirá un permiso que no usa, o
escuchará donde no tiene permiso.

## Por qué no está en la Chrome Web Store

Publicarla exigiría una revisión de Google para una herramienta que van a usar
tres personas una vez. El modo desarrollador es el camino correcto para esto, y
tiene una ventaja: la persona ve el código si quiere, y puede comprobar por sí
misma que los permisos son solo los dos dominios de Effi.
