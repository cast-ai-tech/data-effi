# Poner Master Data en producción

Esta guía es para el desarrollador que va a dejar Master Data funcionando en
internet, y para quien lo tenga que mantener después. Está escrita paso a paso,
sin dar nada por sabido. Si algo aquí ya lo sabes, sáltatelo.

Para correrlo en tu computador mientras desarrollas, no uses esta guía: usa el
[README](README.md), que lo levanta todo con Docker en un solo comando.

> **Regla de esta guía:** aquí van los **nombres** de las variables y dónde se
> cargan. Los **valores** (contraseñas, secretos, claves) nunca van en este
> archivo, ni en el repositorio, ni en un issue. Ver la sección 8.

---

## 1. Dónde vive cada pieza (estado real, 2026-08-24)

| Pieza | Qué es | Dónde vive | Cómo se despliega |
|---|---|---|---|
| **Base de datos** | PostgreSQL con **44 migraciones** (`migrations/001` … `044`) | **Supabase** | Migraciones a mano desde tu máquina (sección 2) |
| **API** | FastAPI (Python). Recibe archivos, calcula KPIs, sirve todo | **Render**, servicio `master-data-api`, plan free, Docker (`render.yaml`) | **Manual Deploy** en Render después de cada push (`autoDeploy: false`) |
| **Worker** | Los jobs de fondo (sincronizar hojas, relink, FX, digest…) | **No corre como servicio.** Los dispara **GitHub Actions** (`.github/workflows/worker-cron.yml`) llamando a la API | Solo con el push del workflow |
| **Frontend** | Next.js 15. Lo que el usuario ve | **Vercel**, proyecto `masterdataweb`, Root Directory `web/` | Automático con cada push a `main`; cada rama/PR tiene su preview |

**Por qué la API no va en Vercel:** Vercel corre funciones que arrancan,
responden y mueren. La API necesita un proceso vivo con un pool de conexiones a
Postgres y una cola de trabajos en memoria. Por eso está en Render.

**Por qué el worker no es un servicio:** el plan free de Render no incluye
Background Workers. En vez de eso `WORKER_ENABLED=false` y GitHub Actions llama
`POST /worker/trigger/{job}` en los horarios del scheduler. De paso mantiene
despierto el servicio, que si no duerme a los 15 minutos sin tráfico.

**Cómo hablan entre sí.** El navegador nunca ve un token: sus peticiones van a
`/api/backend/...` en el mismo dominio de Vercel, y un pequeño servidor dentro
de Next (`web/app/api/backend/[...path]/route.ts`) las reenvía a Render con el
token que vive en una cookie `HttpOnly`. **La única excepción son los
archivos**: van directo del navegador a Render (sección 5), y por eso la API
tiene que autorizar el dominio de Vercel por CORS.

**Orden para un despliegue desde cero.** Hay una dependencia circular: la web
necesita la dirección de la API, y la API necesita la dirección de la web para
dejarla conectarse. Se rompe así:

1. Base de datos (Supabase)
2. API en Render → aquí sale la dirección de la API
3. Web en Vercel → usa esa dirección; aquí sale la dirección de la web
4. Volver a Render, poner la dirección de la web en `CORS_ORIGINS`, redesplegar
5. Cron del worker en GitHub Actions

---

## 2. La base de datos en Supabase

### Paso 1. Crea el proyecto

1. Entra a https://supabase.com y crea una cuenta si no tienes.
2. Haz clic en **New project**.
3. Ponle un nombre, por ejemplo `master-data`.
4. Donde dice **Database Password**, haz clic en **Generate a password** y
   **guárdala en tu gestor de contraseñas ahora mismo**. Supabase no te la vuelve
   a mostrar. Esta es la contraseña del usuario `postgres`.
5. En **Region**, elige `us-east-1` (Virginia): la API en Render está en esa
   misma costa y cada consulta ahorra el viaje.
6. Haz clic en **Create new project** y espera unos dos minutos.

### Paso 2. Copia las dos cadenas de conexión

Una "cadena de conexión" es la dirección completa de la base de datos, con
usuario y contraseña adentro. Master Data usa dos formas distintas de conectarse y
necesitas las dos.

1. Arriba a la derecha, haz clic en **Connect**.
2. Copia estas dos y guárdalas en un bloc de notas:
   - **Session pooler** o **Direct connection** — puerto `5432`
   - **Transaction pooler** — puerto `6543`
3. En ambas, donde dice `[YOUR-PASSWORD]`, reemplázalo por la contraseña del
   paso anterior.
4. A ambas agrégales `?sslmode=require` al final. Supabase solo acepta
   conexiones cifradas y sin esto la conexión falla.

Te deben quedar con esta forma (los valores son de ejemplo):

```
# Puerto 5432 — para migraciones y tareas de administración
postgresql://postgres.<ref>:<PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require

# Puerto 6543 — para la aplicación en marcha
postgresql://postgres.<ref>:<PASSWORD>@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require
```

**Por qué dos.** El puerto 6543 reparte un puñado de conexiones reales entre
muchas peticiones, que es lo que quieres con una API. Pero en ese modo no
sobrevive nada entre una consulta y otra, así que las migraciones — que crean
tablas, roles y funciones — tienen que ir por el 5432.

Master Data es compatible con el puerto 6543 porque el aislamiento por cliente usa
`SET LOCAL` (`api/db.py`, función `connection()`): el `tenant_id` vive dentro de
la transacción y muere con ella, así que una conexión reciclada nunca arrastra el
contexto de un cliente al siguiente. **No cambies eso por un `SET` normal.** Si
lo haces, un cliente empieza a ver los datos de otro.

### Paso 3. Prepara las variables en tu computador

Las migraciones se corren desde tu máquina, apuntando a Supabase.

1. Abre la terminal en la carpeta del proyecto.
2. Copia la plantilla de configuración:
   ```bash
   cp .env.example .env
   ```
3. Genera los secretos que faltan. Cada comando imprime un valor; cópialo al
   `.env` en la línea correspondiente:
   ```bash
   openssl rand -hex 32          # para JWT_SECRET
   openssl rand -hex 32          # para PII_HASH_SALT
   openssl rand -hex 32          # para WORKER_TRIGGER_SECRET
   openssl rand -hex 32          # para PROXY_SHARED_SECRET
   openssl rand -hex 24          # para POSTGRES_APP_PASSWORD
   openssl rand -hex 24          # para POSTGRES_READONLY_PASSWORD
   python -m scripts.generate_pii_key   # imprime la línea PII_ENCRYPTION_KEY= completa
   ```
4. En el `.env`, pon las cadenas de Supabase:
   ```
   POSTGRES_ADMIN_URL=<la del puerto 5432>
   DATABASE_URL=<la del puerto 6543, pero con norte_app — ver paso 5>
   ```

⚠️ **`PII_HASH_SALT` es permanente.** Es lo que convierte el teléfono de un
cliente en un identificador estable. Si lo cambias cuando ya hay datos cargados,
todos los `customer_hash` existentes quedan huérfanos: el sistema deja de
reconocer que dos pedidos son de la misma persona. Genéralo una vez y no lo
vuelvas a tocar.

⚠️ **`PII_ENCRYPTION_KEY` tampoco se cambia.** Es lo que descifra nombres y
teléfonos guardados. Si lo pierdes, esos datos quedan ilegibles para siempre. Los
pedidos, el dinero y las métricas siguen intactos, pero los datos de contacto solo
se recuperan volviendo a subir los archivos originales.

### Paso 4. Aplica las migraciones

Hay **44** archivos en `migrations/`, numerados `001` a `044`. En una base
**nueva**:

```bash
python -m scripts.migrate --status    # muestra qué falta, sin tocar nada
python -m scripts.migrate             # las aplica
```

El script lleva un registro en la tabla `public.schema_migration` (archivo +
checksum), así que solo corre las migraciones nuevas. En una base nueva es
seguro volver a ejecutarlo.

⚠️ **En la base de producción que ya existe, NO corras `scripts.migrate`.** Su
registro `schema_migration` está incompleto (faltan filas de las primeras
migraciones, que se aplicaron a mano), así que el script intentaría
re-aplicarlas y rompería las vistas. Para agregar una migración a producción:
ejecuta el archivo entero contra el puerto 5432 y luego inserta su fila en
`public.schema_migration` (`filename`, `checksum` = sha256 de los bytes del
archivo). Producción va en la **044**.

**Si la migración 007 falla diciendo que no puede crear roles:** esa migración
crea `norte_app` y `norte_readonly`. En Supabase el usuario `postgres` no es
superusuario, pero sí tiene permiso para crear roles, así que normalmente
funciona. Si no, crea los dos roles a mano desde el **SQL Editor** de Supabase
copiando las sentencias `CREATE ROLE` de
`migrations/007_row_level_security.sql`, y vuelve a correr el script.

**Verifica que quedó bien** — esto es lo más importante de todo el despliegue.
Abre el **SQL Editor** en Supabase y corre:

```sql
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('norte_app', 'norte_readonly');
```

Las dos columnas `rolsuper` y `rolbypassrls` tienen que decir `false` en ambas
filas. Si alguna dice `true`, la seguridad por cliente queda anulada: PostgreSQL
exime a los superusuarios de las políticas de fila, y la API vería los datos de
todos los clientes mezclados.

### Paso 5. Pon las contraseñas de los roles

```bash
python -m scripts.setup_roles
```

Esto le asigna a `norte_app` y `norte_readonly` las contraseñas que generaste, y
confirma que ninguno es superusuario.

Ahora arma las cadenas de conexión definitivas de la aplicación, cambiando
`postgres` por el rol correspondiente y su contraseña:

```
DATABASE_URL=postgresql://norte_app.<ref>:<PASSWORD_DE_APP>@aws-0-...:6543/postgres?sslmode=require
DATABASE_URL_READONLY=postgresql://norte_readonly.<ref>:<PASSWORD_READONLY>@aws-0-...:6543/postgres?sslmode=require
```

Fíjate en el punto: a través del pooler de Supabase el usuario se escribe
`rol.referencia-del-proyecto`, no solo `rol`. La referencia del proyecto es lo que
va después de `postgres.` en la cadena que copiaste.

🔒 **La API nunca debe conectarse como `postgres`.** Ese rol es dueño de las
tablas, y PostgreSQL exime al dueño de sus propias políticas de seguridad. Si la
API se conecta así, el aislamiento entre clientes desaparece sin que nada falle
ni avise. `POSTGRES_ADMIN_URL` con el usuario `postgres` se usa solo para
migraciones, desde tu máquina, y no va en ningún servidor.

### Paso 6 (opcional). Datos de demostración

```bash
python -m scripts.seed_demo
```

Solo si quieres ver el dashboard con números antes de cargar datos reales. En una
base que va a producción, sáltatelo.

---

## 3. La API en Render

El repositorio trae el Blueprint completo en **`render.yaml`**: servicio web
`master-data-api`, Docker (`Dockerfile.api`), plan free, región Virginia,
healthcheck en `/health`, y **`autoDeploy: false`**.

### Paso 1. Crea el servicio desde el Blueprint

1. Entra a https://render.com → **New** → **Blueprint**.
2. Conecta el repositorio `cast-ai-tech/master-data` y elige la rama `main`.
3. Render lee `render.yaml` y te pide los valores de las variables marcadas
   `sync: false`. Son estas — **solo nombres aquí**, los valores van del
   gestor de contraseñas al formulario de Render:

| Variable | Qué es |
|---|---|
| `DATABASE_URL` | Cadena del puerto 6543 con `norte_app` (sección 2, paso 5) |
| `DATABASE_URL_READONLY` | Cadena del puerto 6543 con `norte_readonly` |
| `JWT_SECRET` | Generado en la sección 2, paso 3 |
| `PII_HASH_SALT` | Generado; **permanente** |
| `PII_ENCRYPTION_KEY` | Generado; **permanente** |
| `WORKER_TRIGGER_SECRET` | Generado; el mismo va en GitHub (sección 6) |
| `PROXY_SHARED_SECRET` | Generado; el mismo va en Vercel (sección 4) |
| `PUBLIC_API_URL` | La URL pública de este servicio, sin barra final (`https://master-data-api.onrender.com`) |
| `CORS_ORIGINS` | Los dominios de la web, separados por coma, sin barra final. Hoy: la URL de producción en Vercel **y** la de Netlify mientras siga de respaldo |
| `ADVISOR_WHATSAPP` | WhatsApp del asesor que activa planes y arma los «a la medida». Con indicativo y solo dígitos (`573001234567`). Si lo dejas vacío, la pantalla de planes no muestra el botón de contacto |
| `AI_ENABLED`, `GEMINI_API_KEY`, `AI_MODEL` | Solo si vas a activar el copiloto de IA. Se gestionan desde el panel, no desde el Blueprint |

Las demás (`ENVIRONMENT`, `API_HOST`, `API_PORT`, `LOG_LEVEL`,
`WORKER_ENABLED=false`, `MAX_UPLOAD_MB=25`, `INGEST_MAX_CONCURRENCY`,
`UPLOAD_DIR=/tmp/uploads`, `CORS_ORIGIN_REGEX`, `TIER3_FETCH_ENABLED=false`,
`FX_PROVIDER_URL`) ya vienen con valor en `render.yaml`. No las repitas en el
panel: un valor fijo del Blueprint pisa lo que escribas ahí en cada sync.

4. **Apply**. El primer build tarda unos minutos. Al terminar, la dirección es
   `https://master-data-api.onrender.com`.

### Cambio de nombre (agosto 2026): el servicio nuevo convive con el viejo

La plataforma pasó de llamarse Data Effi a **Master Data**. El servicio de Render
se llamaba `data-effi-api` y su URL era `https://data-effi-api.onrender.com`.
Renombrarlo en el sitio cambia la URL al instante y deja la web sin API hasta
que Vercel redespliegue, así que se hace **en paralelo**:

1. Render → **New** → **Blueprint** con este mismo repositorio. `render.yaml` ya
   dice `name: master-data-api`, así que crea un servicio nuevo sin tocar el
   viejo. Carga las mismas variables `sync: false` que tiene `data-effi-api`,
   con `PUBLIC_API_URL = https://master-data-api.onrender.com`.
2. Comprueba `curl https://master-data-api.onrender.com/health`.
3. Vercel → proyecto `masterdataweb` → **Settings → Environment Variables**:
   `NEXT_PUBLIC_API_URL` y `API_URL` = la URL nueva → **Deployments → Redeploy**
   (sin caché). Desde ese momento la web habla con el servicio nuevo.
4. cron-job.org: cambia el host en el ping de `/health` y en `/worker/trigger/*`.
   El workflow `.github/workflows/worker-cron.yml` ya apunta al nuevo.
5. **Webhooks externos.** Las conexiones tipo *Webhook* entregaron a n8n/Make/
   Zapier una URL con el host viejo. Entra a Configuración → Conexiones, copia
   la URL nueva de cada una y pégala en la automatización. La ruta y la clave no
   cambian, solo el dominio.
6. Cuando lleve una semana sin incidentes, apaga o borra `data-effi-api`.

Nadie tiene que volver a entrar: la web sigue leyendo las cookies viejas
(`dataeffi_*`) hasta el 2026-09-15 y las reescribe con el nombre nuevo en la
primera renovación silenciosa. Después de esa fecha, retirar las constantes
`LEGACY_*` de `web/middleware.ts` y `web/app/api/backend/[...path]/route.ts`.

### Paso 2. Cada vez que haya un cambio en la API

`autoDeploy` está en `false`: un push a `main` **no** despliega la API. Hay que
entrar a Render → `master-data-api` → **Manual Deploy** → **Deploy latest
commit**. (La web en Vercel sí se despliega sola.)

### Lo que hay que saber del plan free

- **Duerme a los 15 minutos sin tráfico** y tarda ~30 s en despertar. El cron
  del worker (sección 6) lo mantiene despierto en horario útil.
- **Sin disco persistente.** Los archivos subidos viven en `/tmp/uploads`
  mientras se procesan. Lo ya ingerido está en Postgres y no se ve afectado;
  solo se pierde una carga que estuviera a medio procesar en un redeploy.
- **512 MB de RAM.** `MAX_UPLOAD_MB=25` es seguro porque el upload lee por
  trozos y corta al pasar el límite; `INGEST_MAX_CONCURRENCY=2` evita procesar
  demasiados archivos a la vez.
- **HTTPS lo da Render.** Obligatorio: la web en Vercel se sirve por HTTPS y un
  navegador bloquea toda petición hacia una API en HTTP.

`PUBLIC_API_URL` es obligatoria: detrás de un proxy la API no puede adivinar por
qué dirección la alcanzan desde afuera, y la usa para armar la URL del webhook
que le muestra al operador **una sola vez**. Si está mal, hay que regenerar el
token.

---

## 4. La web en Vercel

### Paso 1. Importa el repositorio

1. Entra a https://vercel.com e inicia sesión con GitHub.
2. **Add New** → **Project**.
3. Busca `cast-ai-tech/master-data` y haz clic en **Import**. Si no aparece,
   **Adjust GitHub App Permissions** y dale acceso a la organización.

### Paso 2. Dile que el frontend está en una subcarpeta

Esto es lo que más se olvida y hace fallar el despliegue.

1. Busca **Root Directory** y haz clic en **Edit**.
2. Elige la carpeta **`web`**.
3. Framework Preset: **Next.js**. Los comandos de build los detecta solo
   (`npm run build`). No los toques.

No hace falta `vercel.json`: el Root Directory es una opción del proyecto (no
se puede fijar por archivo), las cabeceras de seguridad ya salen de
`web/next.config.ts` y `web/middleware.ts`, y no hay rewrites hacia la API
porque el proxy es un route handler de Next (`web/app/api/backend`).

### Paso 3. Variables de entorno

Tres. Márcalas para **Production** y **Preview** (Development es opcional).

| Variable | Qué es |
|---|---|
| `NEXT_PUBLIC_API_URL` | La URL pública de la API en Render, sin barra final. Se **incrusta en el bundle del navegador al compilar**: la usa la pantalla *Cargar* para subir archivos y la de conexiones para comparar la URL del webhook |
| `API_URL` | La misma URL. La lee el servidor de Next (el proxy `/api/backend`) |
| `PROXY_SHARED_SECRET` | **El mismo valor** que en Render. Con él la API distingue a cada navegador para el límite de intentos; sin él funciona, pero todos comparten un solo límite |

⚠️ Si cambias `NEXT_PUBLIC_API_URL`, no basta con editarla: hay que
**Redeploy** para que entre al bundle.

### Paso 4. Despliega

**Deploy**. Al terminar, Vercel te da la URL de producción del proyecto
(`https://masterdataweb.vercel.app` o la que tenga asignada). Cópiala.

### Paso 5. Cierra el círculo — sin esto no cargan archivos

Todas las llamadas del navegador van por el proxy de Next, **menos los
archivos**, que van directo a Render. Para esos el navegador exige que la API
autorice el dominio de Vercel (CORS):

1. Render → `master-data-api` → **Environment** → `CORS_ORIGINS`: agrega la URL
   de producción de Vercel, separada por coma de las que ya estén (Netlify
   sigue ahí mientras sea respaldo). Sin barra al final.
2. **Manual Deploy**.

**Previews de Vercel.** Cada rama y cada PR tiene su propia URL
(`masterdataweb-<algo>.vercel.app`). No hay que agregarlas una a una:
`CORS_ORIGIN_REGEX` en `render.yaml` ya acepta la producción y todos los
previews del proyecto `masterdataweb`:

```
^https://masterdataweb(-[a-z0-9-]+)?\.vercel\.app$
```

Ten en cuenta que un preview habla con la **API de producción** y sus datos
reales. Si eso no te gusta, quita el regex y levanta una segunda API contra
otro proyecto de Supabase para los previews.

🔒 **Endurecer el regex (pendiente, necesita el slug del equipo).** Tal como
está, cualquier proyecto de Vercel que se llame `masterdataweb-<algo>` pasa
CORS, y cualquiera puede crear uno. No hereda ninguna sesión (la API solo
acepta bearer, sin cookies), pero lo correcto es anclarlo al slug del equipo
que Vercel añade al final de cada preview. Abre un preview cualquiera, mira
qué va después del último guion (`masterdataweb-abc123-<team>.vercel.app`) y
pon en `render.yaml`:

```
^https://masterdataweb(-git-[a-z0-9-]+|-[a-z0-9]+)-<team>\.vercel\.app$
```

dejando la URL de producción solo en `CORS_ORIGINS`.

---

## 5. Por qué los archivos van directo a la API

El proxy de Next corre en Vercel como función serverless, y esas funciones
cortan el cuerpo de la petición en **4,5 MB** (en Netlify eran 6 MB, unos 4,5
reales porque el archivo viaja en base64). Un reporte de 5,8 MB daba `413` ahí
mismo, antes de tocar la API.

Por eso la pantalla *Cargar* le pide al proxy solo el **token de acceso**
(`POST /api/backend/auth/upload-credential`; dura 15 minutos y el proxy lo
renueva si está por vencer) y envía el archivo directo a Render con ese token.
El token de refresco (14 días) **nunca sale** de su cookie `HttpOnly`.

Eso exige, en Render: `CORS_ORIGINS` / `CORS_ORIGIN_REGEX` con el dominio de la
web, y `MAX_UPLOAD_MB=25`. Y en la web: `NEXT_PUBLIC_API_URL` correcta, porque
es a esa dirección a la que el navegador envía el archivo.

---

## 6. El cron del worker en GitHub Actions

`.github/workflows/worker-cron.yml` llama `POST /worker/trigger/{job}` en los
mismos horarios (UTC) que `build_scheduler()` en `worker/main.py`:

| Horario UTC | Jobs |
|---|---|
| :05 y :35 de cada hora | `sync_sheets`, `relink_orphans` |
| 06:15 y 18:15 | `sync_tier3` |
| 07:30 | `calibrate_maturation` |
| 10:05 | `refresh_fx` |
| 10:50, 11:50, 12:50, 13:50 | `daily_digest` (resumen por país a las 7 am locales) |

Configuración, una sola vez:

1. GitHub → repositorio → **Settings** → **Secrets and variables** →
   **Actions** → **New repository secret**.
2. Nombre: `WORKER_TRIGGER_SECRET`. Valor: **el mismo** que pusiste en Render.

Disparo manual de un job (útil para probar):

```bash
gh workflow run worker-cron.yml -f jobs=relink_orphans
gh run watch
```

Un job que no aplica todavía (sin conexiones tier 3, sin hojas publicadas)
devuelve 200 y no hace nada. Eso es correcto, no un error.

---

## 7. Comprobar que quedó bien

En orden. Si uno falla, no sigas al siguiente.

1. **La API está viva:**
   ```bash
   curl https://master-data-api.onrender.com/health
   ```
   Debe responder `"status": "ok"`. Si tarda 30 s, estaba dormida; es normal.

2. **La API deja pasar a la web (CORS).** Simula el preflight que hace el
   navegador antes de subir un archivo. Con el dominio de producción de Vercel:
   ```bash
   curl -s -o /dev/null -D - -X OPTIONS https://master-data-api.onrender.com/ingest/upload \
     -H "Origin: https://masterdataweb.vercel.app" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: authorization"
   ```
   Tiene que responder **`HTTP/1.1 200`** con la línea
   `access-control-allow-origin: https://masterdataweb.vercel.app`. Un `400`
   sin esa cabecera significa que ese origen no está en `CORS_ORIGINS` ni
   cumple `CORS_ORIGIN_REGEX`. Repite con la URL de un preview
   (`https://masterdataweb-<algo>.vercel.app`) para comprobar el regex.

3. **El aislamiento por cliente está activo.** Desde el SQL Editor de Supabase:
   ```sql
   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
   WHERE rolname IN ('norte_app', 'norte_readonly');
   ```
   Las cuatro casillas en `false`.

4. **La web abre:** entra a la URL de Vercel. Debe salir la pantalla de inicio
   de sesión.

5. **La web habla con la API:** inicia sesión. Si no entra, abre la consola del
   navegador (F12). Un error que mencione `/api/backend` apunta a `API_URL` en
   Vercel; uno que mencione `CORS` solo puede venir de la subida de archivos y
   apunta a `CORS_ORIGINS` en Render.

6. **Una carga completa:** en `/<país>/cargar`, elige la plataforma, sube un
   archivo y confirma que el job pasa a terminado. Eso prueba el camino directo
   navegador → Render, el disco temporal y la base.

7. **El cron corre:** `gh workflow run worker-cron.yml -f jobs=refresh_fx` y
   `gh run watch` debe terminar en verde.

---

## 8. Lo que se comparte por fuera de GitHub

Ninguno de estos valores va en el repositorio, ni en un issue, ni en un mensaje
de commit. Van por el gestor de contraseñas o un canal privado:

- Contraseña de `postgres` en Supabase
- `POSTGRES_APP_PASSWORD` y `POSTGRES_READONLY_PASSWORD`
- `JWT_SECRET`, `PII_HASH_SALT`, `WORKER_TRIGGER_SECRET`, `PII_ENCRYPTION_KEY`,
  `PROXY_SHARED_SECRET`
- `GEMINI_API_KEY`

Para desarrollar en local no hace falta compartir los de producción: genera unos
propios con los comandos de la sección 2, paso 3.

---

## 9. Cosas que conviene hacer, aunque nadie las pida

- **Backups.** Supabase hace backups automáticos según el plan. En el plan
  gratuito son limitados.
- **Rotar el token del webhook** de cualquier conexión que se haya probado en
  desarrollo antes de pasar a datos reales. Se hace desde la pantalla de
  configuración de la conexión.
- **El token del webhook viaja en la URL** (`/ingest/webhook/{token}`), así que
  queda escrito en los logs de acceso del proxy. Si te molesta, muévelo a un
  encabezado `X-Webhook-Token`; hay que tocar `api/routers/ingest.py` y la
  pantalla que le muestra la URL al operador.
- **`AI_ENABLED=false` hasta que haya presupuesto definido.** El copiloto consume
  tokens de Gemini. `AI_DAILY_TOKEN_BUDGET` pone el techo diario.
- **`TIER3_FETCH_ENABLED=false`** salvo que ya esté registrado el consentimiento
  de cada conexión. Está explicado en el README, sección 5.

---

## Historial: Netlify

Hasta el 2026-08-24 la web vivió en Netlify. Ya no: `netlify.toml` se retiró del
repositorio en el cambio de nombre a Master Data y la web solo se despliega en
Vercel. Si la URL de Netlify sigue en `CORS_ORIGINS` de Render, quítala
(+ Manual Deploy).
