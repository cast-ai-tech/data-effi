# Política de conexiones Tier 3

> Lee esto completo antes de activar una conexión Tier 3 para ti o para un cliente.
> No es papeleo: define qué hace Master Data con tus credenciales y qué riesgo asumes.

## Qué es un Tier

Master Data clasifica cada fuente de datos por **cómo** obtiene la información:

| Tier | Cómo obtiene los datos | Ejemplo | Riesgo |
|------|------------------------|---------|--------|
| **1** | API oficial y documentada, con credenciales que la propia plataforma emite para integraciones | Shopify, Meta Ads, Google Ads | Bajo |
| **2** | Archivo que tú exportas y subes, o que llega a un buzón de correo | Dropi, carga manual Excel/CSV | Bajo |
| **3** | Master Data entra con **tu sesión de usuario** y descarga el mismo reporte que descargarías tú a mano | Effi | **Alto — requiere tu consentimiento explícito** |

## Por qué existe el Tier 3

Algunas plataformas de fulfillment contraentrega en LATAM no publican API. La única
forma de sacar tus propios datos es el botón de "exportar reporte" dentro del panel.
Master Data puede pulsar ese botón por ti — pero solo si tú lo autorizas, y solo bajo las
reglas de abajo.

## Las cinco reglas

### 1. Sin consentimiento, no hay conexión
La base de datos rechaza a nivel de motor cualquier conexión Tier 3 sin una fecha de
consentimiento registrada. No es una validación de formulario que se pueda saltar:
es un trigger en PostgreSQL (`core.enforce_tier3_consent`). Si no autorizaste, no
hay forma de que el sistema consulte en tu nombre.

### 2. Tus credenciales se guardan cifradas, o no se guardan

Hay **dos formas** de conectar una plataforma Tier 3, y no son iguales de seguras.
Te decimos cuál estás usando y qué compra cada una.

**Variable de entorno (`connection.secret_ref`).** Lo que se guarda es el
*nombre* de una variable de entorno, nunca su contenido. La sesión vive en el
entorno del servidor. Si alguien roba un respaldo de la base de datos, no se
lleva ninguna credencial tuya: **no hay nada que llevarse.** Es la opción más
segura que existe y tiene un costo real — cada vez que la sesión vence, alguien
con acceso al servidor tiene que renovarla a mano.

**Bóveda cifrada (`core.connection_credential`).** Tú escribes tu usuario y tu
contraseña en la pantalla de conexiones. Se cifran con AES-128 autenticado
(Fernet) *antes* de tocar una fila, con una llave que **no está en la base de
datos** sino en el entorno del servidor. Es lo que te permite conectar tu cuenta
sin depender de nadie, y a cambio:

| | Variable de entorno | Bóveda cifrada |
|---|---|---|
| Un respaldo robado de la base de datos | no contiene nada | contiene texto cifrado, inútil sin la llave |
| Alguien que se apodera del servidor | tiene la credencial | tiene la credencial |
| Renovar la sesión | a mano, alguien con acceso al servidor | automático |
| Conectar una empresa nueva | requiere un despliegue | lo haces tú en dos minutos |

Sé claro sobre la fila del medio: **contra un atacante que controla el servidor,
ninguna de las dos te protege.** Nada que se pueda guardar sobrevive a eso. Si
esa es tu preocupación, usa la variable de entorno y renuévala a mano, o quédate
en Tier 2 y sube el archivo.

Lo que **nunca** pasa, en ninguno de los dos caminos:

- No existe ningún endpoint que devuelva tu contraseña. Ni para ti, ni para el
  dueño del espacio, ni para soporte. Si la olvidas, la recuperas en la
  plataforma, que es el único sistema que debería poder decírtela.
- Tu contraseña no aparece en ningún registro, ningún mensaje de error y ningún
  reporte de fallo. Los objetos que la transportan se niegan a imprimirse.
- Cuando cambias tu contraseña, la sesión guardada se borra en el mismo
  movimiento. Si la cambiaste porque se filtró, no queremos ser el sitio donde
  la sesión filtrada sigue funcionando.

### 3. Master Data se identifica y no evade nada
El conector envía un `User-Agent` que dice quién es. Espera mínimo 2 segundos entre
peticiones. Si la plataforma responde 401, 403 o redirige al login, **se detiene** y
te pide reautorizar. No rota identidades, no simula un navegador, no reintenta a la
fuerza. Si la plataforma no quiere ese tráfico, la respuesta correcta es parar.

**Y no reintentamos una contraseña rechazada. Nunca.** Si la plataforma dice que
el usuario o la contraseña no sirven, la conexión queda marcada y el sistema deja
de intentar hasta que tú vuelvas a escribirla. Si la plataforma dice que la
cuenta está bloqueada, tampoco insiste. La razón es sencilla: un reintento
automático contra una contraseña equivocada es la forma de **bloquearte a ti de
tu propia cuenta**, mientras duermes, en nombre tuyo. Ese costo lo pagarías tú,
no nosotros, así que la única respuesta aceptable es detenerse.

### 4. Solo lectura, y pedimos el permiso mínimo
El conector pide exportaciones de reportes. Nunca crea, modifica ni cancela nada en
la plataforma de origen.

Eso no es solo una promesa escrita: la lista de permisos que te pedimos vive en
la tabla `core.platform_permission`, y esa tabla tiene una restricción del motor
que **solo acepta `consultar` y `ver_reportes`**. Para que alguien pudiera pedirte
un permiso de escritura tendría que quitar esa restricción a propósito, en una
migración, a la vista de todos. Hay una prueba automática que también lo vigila.

Otras herramientas del mercado te piden Crear, Modificar y Anular porque además
gestionan tus pedidos y contestan tus chats. Master Data lee un reporte. Pedirte
un permiso que nunca vamos a usar sería pedirte confianza a cambio de nada.

**Crea un usuario dedicado, no uses tu cuenta de dueño.** La pantalla de conexión
te muestra exactamente qué permisos necesita ese usuario y qué se pierde si no
das cada uno. Si algún día hay que cortar el acceso, quieres poder borrar un
usuario, no cambiar la contraseña con la que entras tú.

### 5. Mismo camino que una carga manual
Un archivo traído por Tier 3 entra por la misma tubería que uno que subes a mano:
mismo hash de contenido, misma idempotencia, mismas reglas de fusión. Un fetch
automático no tiene ninguna vía privilegiada hacia tus datos.

## Lo que tú asumes al activarlo

Sé directo contigo: esto es lo que estás aceptando.

- **Puede violar los Términos de Servicio de la plataforma.** Muchas plataformas
  prohíben el acceso automatizado, incluso a tus propios datos. Revisa los términos
  de tu proveedor. La decisión y la responsabilidad son tuyas.
- **Tu cuenta podría ser suspendida.** Si la plataforma detecta el acceso
  automatizado y decide sancionarlo, el afectado eres tú.
- **La conexión se va a romper.** Los paneles cambian de rutas sin avisar. Cuando
  pase, verás la conexión en estado `error` en Configuración y tendrás que
  reautorizar o volver a la carga manual mientras se arregla.
- **La sesión expira.** Tendrás que renovarla cada cierto tiempo. Master Data te avisa,
  no lo hace solo.

## La alternativa siempre está disponible

Todo lo que hace el Tier 3 lo puedes hacer con **Tier 2**: exportas el reporte desde
el panel, lo subes a Master Data, y obtienes exactamente los mismos dashboards. Es más
manual y es completamente seguro. Si tienes dudas, empieza por ahí.

## Cómo activar una conexión Tier 3

1. Entra a **Configuración → Conexiones → Nueva conexión**.
2. Elige la plataforma marcada con el candado naranja (Tier 3).
3. Lee la advertencia y marca la casilla de consentimiento. Ese clic queda registrado
   con fecha, hora y tu usuario en `connection.consent_granted_at`.
4. Pulsa **Gestionar** en la conexión. Arriba está la lista de permisos: ve a la
   plataforma, crea un usuario dedicado con esos permisos, y vuelve.
5. Escribe el usuario y la contraseña de **ese** usuario dedicado y guarda. La
   contraseña se cifra antes de guardarse y no se puede volver a ver desde aquí.
6. Pulsa **Probar conexión**. Si falta un permiso, la pantalla te dice cuál y qué
   se pierde sin él; lo agregas en la plataforma y pruebas otra vez.
7. Activa la conexión. La primera sincronización corre en la siguiente pasada del
   worker, o puedes dispararla a mano desde la misma pantalla.

> **Alternativa para quien administra un servidor.** Si prefieres que la
> credencial no esté ni cifrada en la base de datos, guarda la sesión en una
> variable de entorno y ponle el nombre a la conexión en `secret_ref`. Ese camino
> tiene prioridad sobre la bóveda: si ambos existen, se usa la variable de
> entorno. Te tocará renovarla a mano cuando venza.

## Si cambias tu contraseña en la plataforma

Va a pasar, y no es un problema: es el caso normal. Esto es exactamente lo que
ocurre.

**Lo que pasa solo, sin que hagas nada.** La siguiente sincronización intenta
entrar, la plataforma la rechaza, y Master Data hace tres cosas de inmediato:

1. Borra la sesión guardada, que ya no sirve.
2. Marca la conexión como *"usuario o contraseña incorrectos"* y **deja de
   intentar**. No reintenta ni una vez más — insistir con una contraseña
   equivocada es la forma de bloquearte de tu propia cuenta.
3. **Te avisa**, con una notificación crítica en la campana. Esto importa más de
   lo que parece: cuando una conexión se cae, el tablero no se rompe ni muestra
   un error. Se queda con los últimos datos que alcanzó a cargar y **se ve
   perfectamente normal**. Sin ese aviso podrías pasar dos semanas tomando
   decisiones con números congelados y creyendo que fue una temporada floja.

**Lo que haces tú, y toma menos de un minuto.** Configuración → Conexiones →
**Gestionar** → escribe el usuario y la contraseña nueva → **Actualizar cuenta**.
Eso es todo:

- La contraseña nueva reemplaza la vieja, cifrada igual.
- La sesión anterior se borra en el mismo movimiento. Si cambiaste la contraseña
  porque se filtró, no queremos ser el sitio donde la sesión filtrada sigue viva.
- La conexión se reactiva sola: si el worker la había marcado con error, ese
  error se limpia y vuelve a la cola de sincronización.
- El estado queda en *"sin comprobar"*, no en *"conectada"*. Escribir una
  contraseña no demuestra que sirva. Pulsa **Probar conexión** para confirmarlo
  en el momento, en vez de enterarte en la próxima pasada del worker.

**Si cambiaste también los permisos del usuario**, el botón *Probar conexión* te
dice cuál falta antes de que se rompa nada.

> **Consejo.** Si creaste un usuario dedicado solo para Master Data — como
> recomienda la regla 4 — su contraseña no tiene por qué cambiar cuando cambias
> la tuya. Esa es la mitad del valor de tener un usuario aparte: tu rotación de
> contraseñas deja de romper tus integraciones.

## Cómo desactivarla

Configuración → Conexiones → la conexión → **Desactivar**. Deja de consultarse de
inmediato. Los datos ya ingeridos se quedan: son tuyos. Para borrarlos también,
elimina la conexión, y sus guías y movimientos se van con ella.

## Cómo revocar el acceso de verdad

Desactivar la conexión en Master Data detiene a Master Data, y nada más. Para
cortar el acceso de raíz:

1. **Desconecta la cuenta** en Configuración → Conexiones → Gestionar →
   Desconectar. Eso borra la credencial cifrada y la sesión guardada.
2. **Cierra la sesión desde la plataforma de origen** (normalmente "cerrar sesión
   en todos los dispositivos"). Es el único sitio que puede revocarla de verdad.
3. **Cambia la contraseña de ese usuario**, o bórralo, si sospechas que se filtró.
4. Si usabas variable de entorno, **bórrala del servidor** también.

Haz los cuatro pasos si sospechas una filtración. Hacer solo el primero te deja
tranquilo sin estar seguro, que es la peor de las combinaciones.
