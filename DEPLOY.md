# Poner Data Effi en producción

Esta guía es para el desarrollador que va a dejar Data Effi funcionando en
internet. Está escrita paso a paso, sin dar nada por sabido. Si algo aquí ya lo
sabes, sáltatelo.

Para correrlo en tu computador mientras desarrollas, no uses esta guía: usa el
[README](README.md), que lo levanta todo con Docker en un solo comando.

---

## 1. Antes de empezar: Data Effi son cuatro cosas, no dos

Mucha gente asume que con Supabase y Vercel alcanza. No alcanza. Data Effi tiene
cuatro piezas y cada una vive en un lugar distinto:

| Pieza | Qué es | Dónde va |
|---|---|---|
| **Base de datos** | PostgreSQL con 31 migraciones | **Supabase** |
| **API** | FastAPI (Python). Recibe archivos, calcula KPIs, sirve todo | Un servidor propio — **NO Vercel** |
| **Worker** | Proceso Python que procesa las cargas en segundo plano | El mismo servidor de la API |
| **Frontend** | Next.js. Lo que el usuario ve | **Vercel** |

**Por qué la API no puede ir en Vercel:** Vercel corre funciones que arrancan,
responden y mueren. La API de Data Effi necesita lo contrario — un proceso vivo
todo el tiempo, con un pool de conexiones a Postgres y una cola de trabajos en
memoria. El worker directamente no encaja: su trabajo es correr por minutos
después de que la petición ya terminó.

Para la API y el worker sirve cualquiera de estos: Render, Railway, Fly.io,
DigitalOcean App Platform, o un VPS con Docker. El repositorio ya trae los
`Dockerfile` que necesitan.

**Orden en que hay que hacer las cosas.** Hay una dependencia circular: el
frontend necesita saber la dirección de la API, y la API necesita saber la
dirección del frontend para permitirle conectarse. Se rompe así:

1. Base de datos (Supabase)
2. API y worker → aquí sale la dirección de la API
3. Frontend (Vercel) → usa esa dirección; aquí sale la dirección del frontend
4. Volver a la API, agregarle la dirección del frontend y reiniciarla

---

## 2. La base de datos en Supabase

### Paso 1. Crea el proyecto

1. Entra a https://supabase.com y crea una cuenta si no tienes.
2. Haz clic en **New project**.
3. Ponle un nombre, por ejemplo `data-effi`.
4. Donde dice **Database Password**, haz clic en **Generate a password** y
   **guárdala en tu gestor de contraseñas ahora mismo**. Supabase no te la vuelve
   a mostrar. Esta es la contraseña del usuario `postgres`.
5. En **Region**, elige la más cercana a donde están los usuarios.
6. Haz clic en **Create new project** y espera unos dos minutos.

### Paso 2. Copia las dos cadenas de conexión

Una "cadena de conexión" es la dirección completa de la base de datos, con
usuario y contraseña adentro. Data Effi usa dos formas distintas de conectarse y
necesitas las dos.

1. Arriba a la derecha, haz clic en **Connect**.
2. Verás varias opciones. Copia estas dos y guárdalas en un bloc de notas:
   - **Session pooler** o **Direct connection** — puerto `5432`
   - **Transaction pooler** — puerto `6543`
3. En ambas, donde dice `[YOUR-PASSWORD]`, reemplázalo por la contraseña del
   paso anterior.
4. A ambas agrégales `?sslmode=require` al final. Supabase solo acepta
   conexiones cifradas y sin esto la conexión falla.

Te deben quedar así:

```
# Puerto 5432 — para migraciones y tareas de administración
postgresql://postgres.abcdefgh:TU_PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require

# Puerto 6543 — para la aplicación en marcha
postgresql://postgres.abcdefgh:TU_PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require
```

**Por qué dos.** El puerto 6543 reparte un puñado de conexiones reales entre
muchas peticiones, que es lo que quieres con una API. Pero en ese modo no
sobrevive nada entre una consulta y otra, así que las migraciones — que crean
tablas, roles y funciones — tienen que ir por el 5432.

Data Effi es compatible con el puerto 6543 porque el aislamiento por cliente usa
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

```bash
python -m scripts.migrate --status    # muestra qué falta, sin tocar nada
python -m scripts.migrate             # las aplica
```

El script lleva un registro en la tabla `public.schema_migration`, así que solo
corre las migraciones nuevas. Es seguro volver a ejecutarlo.

**Si la migración 007 falla diciendo que no puede crear roles:** esa migración
crea `norte_app` y `norte_readonly`. En Supabase el usuario `postgres` no es
superusuario, pero sí tiene permiso para crear roles, así que normalmente
funciona. Si en tu proyecto no funciona, crea los dos roles a mano desde el **SQL
Editor** de Supabase copiando las sentencias `CREATE ROLE` de
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
DATABASE_URL=postgresql://norte_app.abcdefgh:PASSWORD_DE_APP@aws-0-...:6543/postgres?sslmode=require
DATABASE_URL_READONLY=postgresql://norte_readonly.abcdefgh:PASSWORD_READONLY@aws-0-...:6543/postgres?sslmode=require
```

Fíjate en el punto: a través del pooler de Supabase el usuario se escribe
`rol.referencia-del-proyecto`, no solo `rol`. La referencia del proyecto es lo que
va después de `postgres.` en la cadena que copiaste.

🔒 **La API nunca debe conectarse como `postgres`.** Ese rol es dueño de las
tablas, y PostgreSQL exime al dueño de sus propias políticas de seguridad. Si la
API se conecta así, el aislamiento entre clientes desaparece sin que nada falle
ni avise. `POSTGRES_ADMIN_URL` con el usuario `postgres` se usa solo para
migraciones, desde tu máquina, y no va en el servidor de producción.

### Paso 6 (opcional). Datos de demostración

```bash
python -m scripts.seed_demo
```

Solo si quieres ver el dashboard con números antes de cargar datos reales. En una
base que va a producción, sáltatelo.

---

## 3. La API y el worker

Van juntos, en el mismo servidor. El repositorio ya trae `Dockerfile` para la API
y para el worker, y `docker-compose.yml` sirve de referencia de cómo se conectan.

### Requisitos que no son negociables

1. **HTTPS obligatorio.** El frontend en Vercel se sirve por HTTPS, y un
   navegador bloquea toda petición de una página HTTPS hacia una dirección HTTP.
   Si la API queda en HTTP, el dashboard se ve pero no carga ningún dato y la
   consola del navegador se llena de errores de "mixed content". Casi todos los
   servicios (Render, Railway, Fly) dan HTTPS solo. En un VPS, pon Caddy o Nginx
   con Let's Encrypt adelante.
2. **Dirección pública fija.** Algo como `https://api.tu-dominio.com`.

### Variables de entorno del servidor

```
DATABASE_URL=postgresql://norte_app.xxx:...@...:6543/postgres?sslmode=require
DATABASE_URL_READONLY=postgresql://norte_readonly.xxx:...@...:6543/postgres?sslmode=require

JWT_SECRET=<el que generaste>
PII_HASH_SALT=<el que generaste>
PII_ENCRYPTION_KEY=<el que generaste>
WORKER_TRIGGER_SECRET=<el que generaste>

PUBLIC_API_URL=https://api.tu-dominio.com
CORS_ORIGINS=https://data-effi.vercel.app        # se llena en la sección 4
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO

WORKER_ENABLED=true

# Solo si vas a activar el copiloto de IA
GEMINI_API_KEY=<clave de https://aistudio.google.com/apikey>
AI_ENABLED=true
```

`POSTGRES_ADMIN_URL`, `POSTGRES_PASSWORD` y `POSTGRES_APP_PASSWORD` **no** van
aquí: son para administrar la base, no para correr la aplicación.

`PUBLIC_API_URL` es obligatoria en producción. Detrás de un proxy, la API no
puede adivinar por qué dirección la alcanzan desde afuera, y la usa para armar la
URL del webhook que le muestra al operador — una URL que se muestra **una sola
vez**. Si está mal, el operador se lleva una dirección que no funciona y hay que
regenerar el token.

### Almacenamiento de archivos

La API guarda los archivos subidos en disco antes de procesarlos. Si tu servicio
de hosting borra el disco en cada despliegue, una carga en curso se pierde.
Móntale un volumen persistente en la ruta que use `upload_dir`, o cuenta con que
solo se pierden cargas a medio procesar (los datos ya ingeridos están en la base
y no se ven afectados).

---

## 4. El frontend en Vercel

### Paso 1. Importa el repositorio

1. Entra a https://vercel.com e inicia sesión con GitHub.
2. Haz clic en **Add New** → **Project**.
3. Busca `cast-ai-tech/data-effi` y haz clic en **Import**.
   Si no aparece, haz clic en **Adjust GitHub App Permissions** y dale acceso a
   la organización `cast-ai-tech`.

### Paso 2. Dile que el frontend está en una subcarpeta

Esto es lo que más se olvida y hace fallar el despliegue.

1. Busca **Root Directory** y haz clic en **Edit**.
2. Elige la carpeta **`web`**.
3. El resto (Framework Preset: Next.js, comandos de build) lo detecta solo. No
   lo toques.

### Paso 3. Agrega la variable de entorno

1. Abre la sección **Environment Variables**.
2. Agrega una sola:
   - **Name:** `NEXT_PUBLIC_API_URL`
   - **Value:** `https://api.tu-dominio.com` — la dirección de la sección 3, sin
     barra al final.
3. Déjala marcada para los tres entornos (Production, Preview, Development).

⚠️ Esta variable se incrusta en el código del navegador **en el momento de
compilar**, no se lee al arrancar. Si mañana cambias la dirección de la API, no
basta con editarla en Vercel: hay que volver a desplegar para que tenga efecto.

### Paso 4. Despliega

Haz clic en **Deploy** y espera. Al terminar, Vercel te da una dirección tipo
`https://data-effi.vercel.app`. Cópiala.

### Paso 5. Cierra el círculo — sin esto no funciona nada

El navegador no deja que una página en `data-effi.vercel.app` le hable a
`api.tu-dominio.com` a menos que la API lo autorice explícitamente.

1. Vuelve a la configuración de la API (sección 3).
2. Pon en `CORS_ORIGINS` la dirección exacta que te dio Vercel:
   ```
   CORS_ORIGINS=https://data-effi.vercel.app
   ```
   Sin barra al final. Si tienes varias direcciones, sepáralas con coma.
3. Reinicia la API.

**Sobre las URL de preview:** Vercel le da una dirección distinta a cada rama y a
cada pull request. Ninguna de esas va a estar en `CORS_ORIGINS`, así que los
previews no van a poder hablar con la API de producción. Es lo correcto — no
quieres que una rama a medio hacer escriba en los datos reales. Si quieres
previews funcionales, levanta una segunda API contra un proyecto de Supabase
aparte.

---

## 5. Comprobar que quedó bien

En orden. Si uno falla, no sigas al siguiente.

1. **La API está viva:**
   ```bash
   curl https://api.tu-dominio.com/health
   ```
   Debe responder algo con `"status": "ok"`.

2. **La API llega a la base:** que el `/health` reporte la base conectada, o
   revisa los logs al arrancar — el pool de conexiones se abre al inicio y avisa.

3. **El aislamiento por cliente está activo.** Lo más importante. Desde el SQL
   Editor de Supabase:
   ```sql
   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
   WHERE rolname IN ('norte_app', 'norte_readonly');
   ```
   Las cuatro casillas en `false`.

4. **El frontend abre:** entra a la dirección de Vercel. Debe salir la pantalla
   de inicio de sesión.

5. **El frontend habla con la API:** abre las herramientas de desarrollador del
   navegador (F12), pestaña **Console**, e intenta iniciar sesión. Si ves un
   error que menciona `CORS`, la dirección de Vercel no quedó bien puesta en
   `CORS_ORIGINS`. Si ves `mixed content`, la API está en HTTP y tiene que estar
   en HTTPS.

6. **Una carga completa:** crea una conexión de "Carga manual", sube un archivo
   de prueba y confirma que el estado pasa a terminado. Eso prueba que el worker
   está corriendo y que puede escribir en disco y en la base.

---

## 6. Cosas que conviene hacer, aunque nadie las pida

- **Backups.** Supabase hace backups automáticos según el plan. Verifica que tu
  plan los incluya con la frecuencia que necesitas. En el plan gratuito son
  limitados.
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

## 7. Lo que se comparte por fuera de GitHub

Ninguno de estos valores va en el repositorio, ni en un issue, ni en un mensaje
de commit. Van por el gestor de contraseñas o un canal privado:

- Contraseña de `postgres` en Supabase
- `POSTGRES_APP_PASSWORD` y `POSTGRES_READONLY_PASSWORD`
- `JWT_SECRET`, `PII_HASH_SALT`, `WORKER_TRIGGER_SECRET`, `PII_ENCRYPTION_KEY`
- `GEMINI_API_KEY`

Para desarrollar en local no hace falta compartir los de producción: genera unos
propios con los comandos del paso 3. Los de producción solo se comparten cuando
llega el momento de desplegar.


---

## Sesión con cookies HttpOnly (desde la auditoría del 2026-08-23)

El navegador **ya no llama a la API directamente**. Todas las peticiones van a
`/api/backend/...` en el mismo dominio de la web, y un pequeño servidor dentro de
Next (`web/app/api/backend/[...path]/route.ts`) las reenvía a la API agregando el
token, que vive en una cookie `HttpOnly` que ningún script puede leer.

Eso agrega **dos variables** al despliegue de la web y **una** al de la API:

| Dónde | Variable | Valor |
|---|---|---|
| Netlify (web) | `API_URL` | La URL de la API en Render, p. ej. `https://data-effi-api.onrender.com` |
| Netlify (web) | `PROXY_SHARED_SECRET` | Un secreto nuevo: `openssl rand -hex 32` |
| Render (API) | `PROXY_SHARED_SECRET` | **El mismo valor** que en Netlify |

`NEXT_PUBLIC_API_URL` sigue existiendo (la pantalla de conexiones la usa para
comparar la URL del webhook), y `CORS_ORIGINS` ya no es necesario para la web,
aunque no estorba.

Sin `PROXY_SHARED_SECRET` todo funciona, pero la API ve a todos los navegadores
con la misma dirección (la del servidor de Netlify) y el límite de intentos de
login se comparte entre todos los usuarios. Con el secreto, cada navegador
tiene su propio límite.
