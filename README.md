# Data Effi

> Esta guía te lo pone a andar **en tu computador**. Para dejarlo en internet, con la base de datos en Supabase y el tablero en Vercel, sigue [DEPLOY.md](DEPLOY.md).

## 1. Qué es Data Effi

Data Effi es un tablero que responde una sola pregunta: **de cada guía que despachas,
¿estás ganando o perdiendo plata, y qué haces al respecto?**

En contraentrega (el cliente paga cuando recibe el paquete) una venta **no es plata
hasta que se entrega**. Entre el momento en que alguien te compra y el momento en que
el dinero llega a tu bolsillo, entre el **25% y el 50% de los pedidos no se entregan
nunca**: el cliente no contesta, la transportadora no logra llegar, o rechazan el
paquete en la puerta. Esa plata no se ve en un reporte de ventas, pero sí se ve en tu
cuenta bancaria a fin de mes.

Data Effi toma los reportes que ya exportas hoy de tu transportadora o de tu plataforma de
fulfillment, los junta, y te muestra la verdad por transportadora, por producto, por
ciudad y por país: cuánto entregas de verdad, cuánto te cuesta cada devolución y
cuánto queda al final. Cuando le falta un dato para calcular algo, **te lo dice** en
lugar de inventarse un número.

---

## 2. Qué necesitas antes de empezar

Solo dos cosas.

**Una. Un computador.** Windows, Mac o Linux, cualquiera sirve. Necesita al menos 8 GB
de memoria RAM (casi todos los computadores de los últimos años la tienen).

**Dos. Docker Desktop instalado y encendido.** Docker es *un programa que instala y
enciende todas las piezas del sistema por ti*: la base de datos, el servidor y la
página web. Sin Docker tendrías que instalar cada pieza a mano; con Docker es un solo
comando.

Descárgalo aquí: **https://www.docker.com/products/docker-desktop/**

Instálalo como cualquier otro programa (siguiente, siguiente, aceptar) y **ábrelo**.

**Cómo saber que Docker está encendido:** busca el ícono de una **ballenita** 🐳.

- En **Windows**: abajo a la derecha, en la barra de tareas, junto al reloj. Puede
  estar escondido detrás de la flechita `^`.
- En **Mac**: arriba a la derecha, en la barra de menús.

Haz clic en la ballenita. Si dice **"Docker Desktop is running"** (Docker Desktop está
corriendo), ya estás listo. Si dice "starting" (arrancando), espera un minuto: la
primera vez se demora.

---

## 3. Ponlo a andar en 6 pasos

Haz los pasos en orden. No te saltes ninguno.

### Paso 1. Abre la terminal

La terminal es *una ventana donde escribes órdenes al computador en vez de hacer clic
en botones*. Se ve como una pantalla en blanco o negro con un cursor parpadeando. Da
miedo la primera vez y después no.

- **En Windows:** presiona la tecla `Windows`, escribe `powershell`, y presiona
  `Enter`.
- **En Mac:** presiona `Command` + `barra espaciadora`, escribe `terminal`, y presiona
  `Enter`.

Deja esa ventana abierta. Vas a usarla en todos los pasos que siguen.

### Paso 2. Entra a la carpeta del proyecto

Escribe `cd` (que significa "cambiar de carpeta"), un espacio, y la ruta de la carpeta
donde está Data Effi entre comillas. Por ejemplo:

```
cd "F:\Users\SICOMMER SAS\Documents\Proyectos\dashboard-oswald"
```

Cambia la ruta por la tuya si guardaste el proyecto en otro lado. Presiona `Enter`.

> **Truco:** si no sabes escribir la ruta, escribe `cd ` (con el espacio al final),
> arrastra la carpeta desde el explorador de archivos hasta la ventana de la terminal
> y suéltala. La ruta se escribe sola. Después presiona `Enter`.

### Paso 3. Crea tu archivo de configuración

El proyecto trae una plantilla llamada `.env.example`. Vas a hacerle una copia llamada
`.env`. Ese archivo `.env` es donde viven las **variables de entorno**: *datos de
configuración y contraseñas que el sistema necesita, guardados fuera del código para
que nunca se suban a internet por accidente*.

Copia y pega este comando, y presiona `Enter`:

```
cp .env.example .env
```

No verás ningún mensaje. Eso significa que salió bien.

### Paso 4. Genera las contraseñas

Data Effi necesita seis contraseñas largas y aleatorias. No las inventes tú: una
contraseña que a ti se te ocurre es una contraseña que alguien más puede adivinar.
El computador las genera mejor.

**4.a** Copia y pega este comando, y presiona `Enter`:

```
docker run --rm python:3.12-slim python -c "import secrets; [print(f'{k}={secrets.token_hex(32)}') for k in ('POSTGRES_PASSWORD','POSTGRES_APP_PASSWORD','POSTGRES_READONLY_PASSWORD','JWT_SECRET','PII_HASH_SALT','WORKER_TRIGGER_SECRET')]"
```

La primera vez se demora un momento porque descarga lo que necesita. Al terminar te
imprime seis líneas parecidas a estas (las tuyas serán distintas, y así debe ser):

```
POSTGRES_PASSWORD=d79b...
POSTGRES_APP_PASSWORD=a41c...
POSTGRES_READONLY_PASSWORD=9fe0...
JWT_SECRET=e8eb...
PII_HASH_SALT=7b32...
WORKER_TRIGGER_SECRET=c05a...
```

> **¿Prefieres generarlas de otra forma?** Si tu computador tiene `openssl` (los Mac y
> los Linux lo traen de fábrica), este comando genera una contraseña a la vez:
> `openssl rand -hex 32`. Tendrás que correrlo seis veces, una por cada nombre de la
> lista de arriba.

**4.b** Selecciona esas seis líneas con el mouse y cópialas.

**4.c** Abre el archivo `.env` que creaste en el paso 3. Está en la misma carpeta del
proyecto. Ábrelo con el Bloc de notas (Windows) o con TextEdit (Mac): haz clic
derecho sobre el archivo → *Abrir con*.

**4.d** Dentro del archivo vas a ver esas seis palabras ya escritas, con el signo `=`
y nada después. Reemplaza cada línea vacía por la línea completa que generaste. Por
ejemplo, donde dice:

```
JWT_SECRET=
```

debe quedar:

```
JWT_SECRET=e8eb...
```

Sin espacios antes ni después del `=`. Sin comillas.

**4.e** Guarda el archivo y ciérralo.

> **Esto es lo más importante de todo el documento:** ese archivo `.env` contiene las
> llaves de tu operación. **Nunca** lo mandes por WhatsApp, ni por correo, ni lo subas
> a Google Drive, ni lo publiques en internet. Si alguien lo tiene, tiene tus datos.
> El proyecto ya está configurado para que `.env` jamás se suba a un repositorio, pero
> de tus copias manuales respondes tú.
>
> Una advertencia más sobre `PII_HASH_SALT`: esa llave es la que protege los teléfonos
> y documentos de tus clientes. **Si la cambias después de haber cargado datos, Data Effi
> pierde el rastro de todos los clientes que ya tenía.** Genérala una vez y no la
> toques nunca más.

### Paso 5. Enciende Data Effi

Copia y pega este comando, y presiona `Enter`:

```
docker compose up -d --build
```

**La primera vez esto se demora entre 5 y 15 minutos.** Docker está descargando y
armando cada pieza. Vas a ver montones de texto pasando por la pantalla: es normal, no
lo interrumpas. Cuando termine, verás algo como `Started` o `Running` al lado de los
nombres `dataeffi_db`, `dataeffi_api`, `dataeffi_worker` y `dataeffi_web`.

Las siguientes veces arranca en segundos.

### Paso 6. Carga los datos de demostración

Data Effi arranca vacío. Este comando le mete una operación de ejemplo de tres países para
que puedas ver cómo se ve todo funcionando antes de meter tus datos reales.

Copia y pega, y presiona `Enter`:

```
docker compose exec -T api python -m scripts.seed_demo --reset
```

> **Si tienes `make` instalado** (una herramienta de atajos que traen algunos
> computadores, sobre todo Mac y Linux), el mismo paso se escribe más corto:
> `make seed-demo`. Hace exactamente lo mismo. Si no sabes si lo tienes, usa el
> comando largo de arriba y listo.

Al terminar te imprime un resumen con las guías de cada país y te dice cuántos widgets
quedaron bloqueados (eso también es a propósito, lo explicamos abajo).

---

### Ya está: entra a Data Effi

Abre tu navegador (Chrome, Edge, Safari, el que uses) y ve a:

**http://localhost:3000**

Entra con estos datos:

| | |
|---|---|
| **Correo** | `demo@dataeffi.co` |
| **Contraseña** | `demo-dataeffi-2026` |

### Qué deberías ver

Lo primero que aparece es la pantalla **Global**: los tres países de la demostración
(Ecuador, Colombia y Guatemala) con las cifras que importan de cada uno — cuántas
guías despachaste, cuántas se entregaron, cuántas se devolvieron y cuánta plata quedó
al final. Abajo, un ranking de países ordenado por resultado.

En el menú de la izquierda puedes entrar a cada país por separado y ver el detalle:
tabla de transportadoras, tabla de productos, semáforo de ciudades, la curva de cuánto
tarda cada guía en resolverse, y la cascada que muestra dónde se va la plata desde el
valor despachado hasta lo que realmente queda.

Vas a notar que **algunos recuadros aparecen grises, borrosos y con un candado**. Eso
no es un error ni una pantalla rota: es Data Effi diciéndote *"para calcular esto me falta
que conectes tu cuenta de publicidad"*. En la demostración, Colombia y Guatemala no
tienen conexión de pauta a propósito, para que veas cómo se comporta.

---

## 4. Tu primer reporte real

Cuando ya viste cómo funciona la demostración, es hora de meter tus propios datos.
Son tres pasos.

### Paso 1. Crea una conexión de "Carga manual"

Una **conexión** es el nombre que le pones a una fuente de datos: "de aquí vienen mis
guías de Colombia".

1. En el menú de la izquierda, haz clic en **Configuración**.
2. En la sección **Países**, activa el país donde operas.
3. Baja hasta la sección **Conexiones** y haz clic en **Nueva conexión**.
4. Elige tu país.
5. En la lista de plataformas, elige **Carga manual Excel/CSV**.
6. Ponle un nombre que reconozcas, por ejemplo `Guías Colombia`.
7. Guarda.

### Paso 2. Sube tu archivo

1. En el menú de la izquierda, haz clic en **Cargar datos**.
2. Arriba, elige la conexión que acabas de crear.
3. Al lado, en **Tipo de reporte**, elige **Guías**.
4. Arrastra tu archivo de Excel o CSV hasta el recuadro punteado, o haz clic ahí para
   buscarlo en tu computador.

Data Effi acepta archivos `.csv`, `.xlsx`, `.xlsm`, `.txt` y `.tsv`, de hasta 25 MB cada
uno, y hasta 20 archivos a la vez. El progreso aparece en esa misma pantalla.

### Paso 3. Revisa el resultado de la carga

Mientras procesa, cada archivo muestra su estado: *En cola*, *Procesando…* y al final
*Listo*, *Ya estaba cargado* o *Falló*.

Más abajo, en el historial de cargas, haz clic en tu archivo. Ahí ves cuántas filas
leyó, cuántas eran nuevas, y una línea que dice **"Columnas ignoradas"** con el nombre
exacto de cada columna que Data Effi no supo interpretar.

### Si exportas desde Effi ERP: no tienes que hacer nada

Data Effi **reconoce los dos reportes de Effi tal como salen**, sin que toques una sola
columna. Cuando subas uno, la pantalla te lo dice: *"Detectado: Effi · Reporte de guías
de transporte"*.

| Reporte de Effi | Qué trae | Súbelo como |
|---|---|---|
| `Reporte de Guías de transporte AAAA-MM-DD.xlsx` | 87 columnas: guías, estados, destinos, fletes y valores | **Guías** |
| `Reporte de movimientos de dinero Effi ....xls` | 56 columnas: el movimiento de tu Wallet, plata que entra y sale | **Movimientos de dinero** |

**Sube los dos.** Con las guías solas, Data Effi estima tus costos; con los movimientos,
usa la plata real que entró y salió de tu Wallet. La diferencia entre "estimado" y
"real" es exactamente la diferencia entre un tablero bonito y uno en el que puedes
confiar para decidir.

Tres cosas que Data Effi hace por ti con estos archivos:

1. **El "`.xls`" de movimientos no es un Excel de verdad** — Effi lo exporta como una
   tabla de página web con nombre de Excel. Data Effi lo detecta por dentro y lo lee igual.
   No necesitas abrirlo ni convertirlo.
2. **Los cruza solos.** El reporte de dinero solo menciona el número de la
   transportadora (`LC54718007`), nunca el número interno de Effi. Data Effi los amarra por
   ese número, así que cada peso queda pegado a su guía.
3. **Separa "entregado" de "cobrado".** Effi marca la liquidación aparte: una guía
   entregada el lunes puede pagarse el viernes. Data Effi te muestra las dos fechas y cuánta
   plata está entregada pero todavía no liquidada.

Además distingue un estado que casi nadie mira: **"Disponible para retiro en oficina"**.
En un reporte real de 1.649 guías, **278 estaban ahí** — ni entregadas ni devueltas,
esperando que el cliente pasara a recogerlas. Es la plata más fácil de recuperar que
tienes, y por eso Data Effi no la mete en el montón de "novedad".

### Si usas Effi Y Dropi: cada uno en su conexión

Si despachas por dos plataformas (por ejemplo Effi y Dropi), no las mezcles en una
sola carga. Crea **una conexión para cada una** (Configuración → Conexiones → elige
"Effi" o "Dropi") y sube el reporte de cada plataforma a su propia conexión.

¿Por qué? Porque así el tablero puede separarlas. En la parte de arriba de cada
pantalla aparece un botón **Todas / Effi / Dropi**: al elegir una, todas las tarjetas
muestran solo las guías de esa plataforma. Y en la pestaña **Logística** verás:

- **Plataformas**: cuántas guías lleva cada una, qué porcentaje del total es, y cómo
  le va en entregas y devoluciones.
- **Resumen diario por estados**: día por día, cuántas guías se entregaron, cuántas
  se devolvieron, cuántas siguen en camino y cuántas tienen novedad. Un bloque por
  plataforma, con su fila de TOTAL GENERAL.

Si una tarjeta no puede separar por plataforma (por ejemplo la de pauta, porque los
anuncios no son de Effi ni de Dropi), te lo dice con una franja encima: "Todas las
plataformas". Nunca te va a mostrar un número mezclado como si fuera de una sola.

**El informe para compartir.** Arriba a la derecha del tablero de cada país está el
botón **Informe diario**. Abre una página lista para imprimir con el bloque de cada
plataforma, la tabla diaria y el consolidado. Clic en **Guardar en PDF** y te lo
descarga (o te abre la ventana de imprimir de tu navegador, donde eliges "Guardar
como PDF"). Cada mañana también te llega un aviso en la campanita 🔔 con el informe
de los últimos 14 días ya listo para abrir.

Dos detalles que el informe hecho a mano no tiene y este sí:

1. **Dos porcentajes de devolución.** "% devol." divide las devoluciones por todas las
   guías del día, como en el informe manual. "% devol. cerradas" divide solo por las
   guías que ya terminaron (entregadas o devueltas): es el número que se cumple cuando
   el día termina de madurar. Un día reciente con 4 guías y 2 en camino puede decir
   "50 %" en el primero y no significar nada; el segundo lleva una **~** para avisarte
   que hay menos de 10 guías cerradas y es un estimado.
2. **Los días sin guías aparecen en cero**, no desaparecen de la tabla.

### Qué columnas necesita tu archivo (si NO viene de Effi)

Para un Excel armado a mano o de otra plataforma, no tienes que renombrar nada. Data Effi ya conoce las formas más
comunes en que viene escrita cada columna en LATAM. Estas son:

| Qué es | Cómo puede llamarse la columna en tu archivo | ¿Obligatoria? |
|---|---|---|
| Número de guía | `guía`, `numero guia`, `número de guía`, `no guia`, `nro guia`, `tracking`, `tracking number`, `código guía`, `guía número` | **Sí** |
| Fecha de creación | `fecha`, `fecha creación`, `fecha de creación`, `fecha guía`, `fecha generación`, `creado` | No |
| Estado | `estado`, `estatus`, `status`, `estado guía`, `estado actual` | No |
| Transportadora | `transportadora`, `transportador`, `carrier`, `operador logístico`, `empresa envío` | No |
| Ciudad de destino | `ciudad`, `ciudad destino`, `municipio`, `ciudad de entrega` | No |
| Departamento / estado / provincia | `departamento`, `depto`, `estado destino`, `provincia`, `región` | No |
| Producto | `producto`, `nombre producto`, `artículo`, `item`, `descripción producto` | No |
| Valor a recaudar | `valor`, `valor recaudo`, `valor a recaudar`, `valor declarado`, `total`, `total pedido`, `monto`, `precio venta`, `valor total` | No |
| Valor recaudado | `recaudado`, `valor recaudado`, `monto recaudado`, `cobrado` | No |
| Flete | `flete`, `costo flete`, `valor flete`, `envío`, `costo envío` | No |
| Flete de devolución | `flete devolución`, `costo devolución`, `valor devolución` | No |
| Costo del producto | `costo producto`, `costo`, `costo proveedor`, `cogs` | No |
| Cantidad | `cantidad`, `unidades`, `qty`, `cant` | No |
| Teléfono o documento del cliente | `teléfono`, `celular`, `documento`, `cédula`, `identificación`, `nit`, `móvil` | No |
| Proveedor | `proveedor`, `supplier`, `bodega` | No |
| Tienda o marca | `tienda`, `store`, `cuenta`, `marca` | No |
| Moneda | `moneda`, `divisa`, `currency` | No |
| Fecha de entrega | `fecha entrega`, `fecha de entrega`, `entregado el` | No |
| Fecha de devolución | `fecha devolución`, `fecha de devolución`, `devuelto el` | No |

No importan las mayúsculas ni las tildes: `CIUDAD`, `Ciudad` y `ciudad` son lo mismo
para Data Effi.

**La única columna obligatoria es el número de guía.** Sin ella no hay forma de saber
de qué envío habla cada fila, y el archivo se rechaza completo.

**Las columnas que Data Effi no reconoce se te reportan, nunca se ignoran en silencio.**
Si tu archivo trae una columna `Valor Neto` que Data Effi no supo interpretar, te la lista
en el reporte de la carga. Un tablero que descarta columnas calladito es un tablero
que miente.

**Lo mismo pasa con los estados.** Si tu transportadora usa una palabra que Data Effi no
conoce, te la reporta en vez de adivinar. Adivinar un estado dañaría todos tus
porcentajes de entrega.

**Subir el mismo archivo dos veces es completamente seguro.** Data Effi le calcula una
huella digital a cada archivo. Si ya lo había cargado, te dice "ya estaba cargado" y no
duplica ni una sola guía. Puedes volver a subir el reporte de la semana pasada sin
miedo, y puedes subir reportes que se solapan: la misma guía que aparece en diez
archivos distintos sigue siendo una sola fila.

---

## 5. Los tres tipos de conexión

Data Effi clasifica cada fuente de datos por **cómo** consigue la información.

| Tier | Cómo consigue los datos | Ejemplos | ¿Necesita tu permiso? | Riesgo |
|---|---|---|---|---|
| **Tier 1** | Por la API oficial de la plataforma. Una **API** es *una puerta que la propia plataforma abre para que otros programas se conecten*. Es el camino que ellos mismos diseñaron. | Shopify, Meta Ads, TikTok Ads, Google Ads | No | Bajo |
| **Tier 2** | Por archivo. Tú exportas el reporte desde tu panel y lo subes a Data Effi, o llega a un buzón de correo. | Dropi, carga manual de Excel/CSV, hoja de confirmación | No | Bajo |
| **Tier 3** | Data Effi entra **con tu propia sesión de usuario** y descarga el mismo reporte que tú descargarías a mano. | Effi | **Sí, obligatorio** | **Alto** |

### ⚠️ Antes de activar una conexión Tier 3, lee esto

> **Una conexión Tier 3 usa tu propia sesión de usuario en la plataforma.** No es una
> integración que la plataforma haya aprobado: es Data Effi pulsando por ti el botón de
> "exportar reporte" dentro de tu panel.
>
> **Puede violar los Términos de Servicio de esa plataforma.** Muchas plataformas
> prohíben el acceso automatizado, incluso a tus propios datos.
>
> **Tu cuenta podría ser suspendida.** Si la plataforma detecta el acceso automatizado
> y decide sancionarlo, el afectado eres tú.
>
> **La responsabilidad de esa decisión es tuya, no de Data Effi.** Data Effi te pide un
> consentimiento explícito, lo registra con fecha y hora, y no consulta nada sin él.
> Pero la decisión de asumir ese riesgo la tomas tú.
>
> **El Tier 2 hace exactamente el mismo trabajo, sin ningún riesgo.** Exportas el
> reporte desde el panel, lo subes a Data Effi, y obtienes los mismos tableros. Es un poco
> más manual y es completamente seguro. **Si tienes cualquier duda, usa Tier 2.**

Lee la política completa antes de decidir: **[docs/tier3-politica.md](docs/tier3-politica.md)**.
Ahí está explicado qué hace Data Effi con tus credenciales, cómo se detiene si la
plataforma lo rechaza, y cómo revocarle el acceso de verdad.

---

## 6. Si algo sale mal

| Qué ves | Qué significa | Qué haces |
|---|---|---|
| `Cannot connect to the Docker daemon`, `docker: command not found`, o `error during connect` | Docker no está encendido, o no está instalado. | Abre Docker Desktop y espera a que la ballenita 🐳 diga *"Docker Desktop is running"*. Después vuelve a correr `docker compose up -d --build`. Si el computador no lo tiene instalado, instálalo desde https://www.docker.com/products/docker-desktop/ |
| `port is already allocated` o `bind: address already in use` con el número **3000** | Otro programa de tu computador ya está usando la puerta 3000. | Abre tu archivo `.env`, agrega al final la línea `WEB_PORT=3001`, guarda, y corre `docker compose up -d` otra vez. Ahora Data Effi vive en **http://localhost:3001** |
| El mismo error, pero con el número **5433** | Ya tienes otra base de datos PostgreSQL corriendo en tu computador. | En tu archivo `.env`, cambia la línea `POSTGRES_PORT=5433` por `POSTGRES_PORT=5434`, guarda, y corre `docker compose up -d` otra vez. |
| `POSTGRES_APP_PASSWORD is required`, `JWT_SECRET is required`, o **`Data Effi no puede arrancar: falta configuración`** | Falta llenar una contraseña en el archivo `.env`, o quedó con el texto de relleno `CHANGE_ME`. | El mensaje te dice **exactamente cuál** falta. Vuelve al **Paso 4**, genera los valores y pégalos. Ojo: `JWT_SECRET`, `PII_HASH_SALT` y `WORKER_TRIGGER_SECRET` deben tener mínimo 32 caracteres, y las contraseñas de base de datos mínimo 16. El comando del paso 4 ya las genera del tamaño correcto. |
| La página abre pero no hay ningún dato: todo en cero o pantallas vacías | Data Effi está funcionando, pero todavía no tiene información que mostrar. | Tres cosas que revisar, en este orden: (1) ¿corriste el comando del **Paso 6**? (2) ¿activaste al menos un país en **Configuración**? (3) ¿creaste una conexión y subiste un archivo en **Cargar datos**? |
| Un recuadro aparece **gris, borroso y con un candado** | **Esto es correcto, no es un error.** Data Effi te está diciendo qué conector le falta para poder calcular ese número. Por ejemplo, el costo por venta necesita tus datos de publicidad; sin la cuenta de pauta conectada, no puede calcularlo. | Si quieres ese recuadro, ve a **Configuración → Conexiones** y crea la conexión que el mensaje del candado te indica. Si no la necesitas, déjalo así: Data Effi prefiere mostrarte un candado honesto antes que un número inventado. |
| Al subir un archivo te dice **"ya estaba cargado"** | **Esto también es correcto.** Ese archivo exacto ya había entrado antes. Data Effi lo reconoció por su huella digital y no lo procesó de nuevo. | Nada. Es la respuesta que debe dar. Si de verdad son datos nuevos, exporta el reporte otra vez desde tu plataforma: si el contenido cambió aunque sea en una fila, Data Effi lo trata como archivo nuevo. |
| La página abre pero dice que no puede conectarse al servidor, o todo se queda cargando | El servidor (la API) no arrancó bien. | Corre `docker compose logs api --tail=50` y lee las últimas líneas: ahí está el motivo. Casi siempre es una variable faltante en `.env`. |
| Cambiaste `API_PORT` en `.env` y ahora la web no trae datos | La página web guarda la dirección del servidor en el momento en que se construye. | En `.env`, ajusta también `NEXT_PUBLIC_API_URL` al puerto nuevo (por ejemplo `http://localhost:8001`) y reconstruye con `docker compose up -d --build`. |
| Quieres empezar completamente de cero | — | `docker compose down -v` borra **todos los datos**, incluidos los tuyos. Es irreversible. Después vuelve al **Paso 5**. |

**Para apagar Data Effi sin perder nada:** `docker compose down`. Los datos se quedan
guardados y vuelven cuando lo enciendas otra vez.

---

## 7. Para quien sepa de código

Para desplegar en producción — Supabase para la base, Vercel para el frontend, y un servidor aparte para la API y el worker — está todo en [DEPLOY.md](DEPLOY.md).

### Arquitectura

- **PostgreSQL 16** es el producto, no el almacén: todos los KPI viven en vistas
  `mart.*` y la API no calcula métricas. El aislamiento multi-tenant es por `tenant_id`
  del JWT **más** Row-Level Security sobre la variable de sesión `norte.tenant_id` —
  sin ella las vistas devuelven cero filas (falla cerrado, no abierto).
- **API FastAPI** (`api/`): autenticación con JWT, ingesta sobre una cola acotada,
  KPIs, configuración, capa de IA (Google Gemini, con NL→SQL contra un rol de solo
  lectura restringido a `mart`) y disparador del worker.
- **Pipeline de ingesta** (`pipeline/`): un solo camino para todo archivo, venga de
  subida manual, buzón o fetch Tier 3. Idempotencia por `sha256(bytes)`, merge que
  nunca retrocede un estado terminal, y columnas y estados no reconocidos reportados
  en vez de descartados.
- **Worker APScheduler** (`worker/`): relink de movimientos huérfanos, tasas FX,
  calibración de maduración y sincronización Tier 3, todos protegidos con advisory
  locks de PostgreSQL.
- **Frontend Next.js 15 + React 19** (`web/`): el layout de cada país lo dicta
  `mart.v_country_dashboard_layout` con estados `available` / `degraded` / `blocked`;
  el frontend no decide qué mostrar, pinta lo que la base le dice.

### Comandos

Cada atajo de `make` tiene su equivalente directo, porque en Windows `make` no viene
instalado.

| Qué hace | Con `make` | Sin `make` |
|---|---|---|
| Levantar toda la plataforma | `make up` | `docker compose up -d --build` |
| Apagar (conservando datos) | `make down` | `docker compose down` |
| Ver logs en vivo | `make logs` | `docker compose logs -f --tail=100` |
| Aplicar migraciones | `make migrate` | `docker compose run --rm migrate` |
| Crear/actualizar roles de base de datos | `make roles` | `docker compose exec -T api python -m scripts.setup_roles` |
| Cargar la demo | `make seed-demo` | `docker compose exec -T api python -m scripts.seed_demo --reset` |
| Ejecutar un job del worker | `make worker-job JOB=refresh_fx` | `docker compose exec -T worker python -m worker.main refresh_fx` |
| Abrir `psql` | `make shell-db` | `docker compose exec db psql -U norte -d norte` |
| Todos los tests | `make test` | `python -m pytest -q` y `cd web && npm test` |
| Tests de backend | `make test-backend` | `python -m pytest -q` |
| Tests de frontend | `make test-frontend` | `cd web && npm test` |
| Tests end-to-end | `make test-e2e` | `cd web && npx playwright test` |
| Lint | `make lint` | `python -m ruff check .` y `cd web && npx eslint .` |
| Verificación de tipos | `make typecheck` | `python -m mypy api pipeline worker ai --ignore-missing-imports` y `cd web && npx tsc --noEmit` |
| Auditoría de dependencias | `make audit` | `python -m pip_audit` y `cd web && npm audit --omit=dev` |
| Construir imágenes sin levantar | `make build` | `docker compose build` |

Jobs disponibles para `worker.main`: `relink_orphans`, `refresh_fx`,
`calibrate_maturation`, `sync_tier3`.

Las migraciones son archivos `.sql` planos en `migrations/`, aplicados en orden
alfabético por `scripts/migrate.py` y escritos para ser idempotentes — el servicio
`migrate` los vuelve a correr en cada arranque del stack, y `api` y `worker` esperan a
que termine antes de levantar.

Los tests marcados con `@pytest.mark.postgres` necesitan una base viva: toman el DSN de
`TEST_DATABASE_URL` (o `DATABASE_URL`) y crean/destruyen `norte_test` con el
superusuario de `POSTGRES_ADMIN_URL`. Para correrlos sin levantar todo el stack:
`docker compose -f docker-compose.dev.yml up -d`, que arranca solo PostgreSQL.

### URLs y puertos

| Servicio | URL | Puerto en tu máquina | Variable que lo cambia |
|---|---|---|---|
| Web (Next.js) | http://localhost:3000 | 3000 | `WEB_PORT` |
| API (FastAPI) | http://localhost:8000 | 8000 | `API_PORT` |
| Documentación OpenAPI | http://localhost:8000/docs | — | — |
| Estado del servicio | http://localhost:8000/health | — | — |
| PostgreSQL | `localhost:5433` | 5433 | `POSTGRES_PORT` |

### Variables de entorno

Todas viven en `.env`, generado a partir de la plantilla `.env.example`, que documenta
una por una para qué sirve. `.env` está en `.gitignore` y nunca debe versionarse.

`docker-compose.yml` construye `DATABASE_URL`, `DATABASE_URL_READONLY` y
`POSTGRES_ADMIN_URL` a partir de las contraseñas, apuntando al host `db` de la red
interna de Docker. Las líneas `DATABASE_URL=...` del `.env` que traen `CHANGE_ME` solo
importan si corres la API fuera de Docker.

Obligatorias para arrancar: `POSTGRES_PASSWORD`, `POSTGRES_APP_PASSWORD`,
`JWT_SECRET`, `PII_HASH_SALT` y `WORKER_TRIGGER_SECRET`. Compose falla con un mensaje
explícito si falta alguna, y la API se niega a arrancar con un secreto corto o con el
valor de relleno todavía puesto.

`PUBLIC_API_URL` pasa a ser obligatoria en cuanto publiques Data Effi detrás de un
dominio o un proxy (Nginx, Traefik, Cloudflare). Es la dirección con la que te ven
**desde afuera**, por ejemplo `https://api.tu-dominio.com`. Data Effi la usa para armar
la URL del webhook que le pegas a n8n, Make o Zapier. Si la dejas vacía, esa URL se
arma con la dirección interna del contenedor (`http://api:8000`), que tu automatización
no puede alcanzar — y el token del webhook se muestra **una sola vez**, así que no hay
oportunidad de corregirla después. En local, con `docker compose up`, déjala vacía.

`POSTGRES_READONLY_PASSWORD` pasa a ser obligatoria en cuanto habilites la capa de IA
(`AI_ENABLED=true` más `GEMINI_API_KEY`): el NL→SQL se niega a correr sin un rol de
solo lectura.

La capa de IA usa **Google Gemini**. La llave se saca en
https://aistudio.google.com/apikey, se pega en `GEMINI_API_KEY` dentro de tu `.env`, y
no se comparte ni se sube a Git. El modelo por defecto es `gemini-2.5-flash` y se
cambia con `AI_MODEL`. `AI_DAILY_TOKEN_BUDGET` pone un techo de gasto por día y por
tenant: cuando se agota, el copiloto lo dice y los tableros siguen funcionando igual.
Con `AI_ENABLED=false` Data Effi arranca sin llave y sin copiloto; nada más se ve afectado.

### Documentación

| Documento | Qué contiene |
|---|---|
| [docs/estructura-analisis-ecommerce.md](docs/estructura-analisis-ecommerce.md) | Qué mide Data Effi y por qué. La definición exacta detrás de cada número: las dos tasas de entrega, la escalera de estados, la maduración de cohortes, la cascada de contribución. |
| [docs/arquitectura-multipais-conectores.md](docs/arquitectura-multipais-conectores.md) | Cómo el país es dato y no código, las capas tenant → país → tienda → conexión, los tiers de conector, la degradación honesta y la idempotencia. |
| [docs/tier3-politica.md](docs/tier3-politica.md) | Las cinco reglas de las conexiones por sesión, qué asumes al activarlas y cómo revocarlas de verdad. |
| [docs/plataformas-effi-dropi.md](docs/plataformas-effi-dropi.md) | Effi y Dropi lado a lado: la plataforma como filtro y como dimensión (migraciones 040/041), los cinco grupos de estado, los dos porcentajes de devolución y el informe diario imprimible. |
