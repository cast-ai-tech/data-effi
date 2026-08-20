# Política de conexiones Tier 3

> Lee esto completo antes de activar una conexión Tier 3 para ti o para un cliente.
> No es papeleo: define qué hace Norte con tus credenciales y qué riesgo asumes.

## Qué es un Tier

Norte clasifica cada fuente de datos por **cómo** obtiene la información:

| Tier | Cómo obtiene los datos | Ejemplo | Riesgo |
|------|------------------------|---------|--------|
| **1** | API oficial y documentada, con credenciales que la propia plataforma emite para integraciones | Shopify, Meta Ads, Google Ads | Bajo |
| **2** | Archivo que tú exportas y subes, o que llega a un buzón de correo | Dropi, carga manual Excel/CSV | Bajo |
| **3** | Norte entra con **tu sesión de usuario** y descarga el mismo reporte que descargarías tú a mano | Effi | **Alto — requiere tu consentimiento explícito** |

## Por qué existe el Tier 3

Algunas plataformas de fulfillment contraentrega en LATAM no publican API. La única
forma de sacar tus propios datos es el botón de "exportar reporte" dentro del panel.
Norte puede pulsar ese botón por ti — pero solo si tú lo autorizas, y solo bajo las
reglas de abajo.

## Las cinco reglas

### 1. Sin consentimiento, no hay conexión
La base de datos rechaza a nivel de motor cualquier conexión Tier 3 sin una fecha de
consentimiento registrada. No es una validación de formulario que se pueda saltar:
es un trigger en PostgreSQL (`core.enforce_tier3_consent`). Si no autorizaste, no
hay forma de que el sistema consulte en tu nombre.

### 2. Tus credenciales nunca tocan la base de datos
Lo que Norte guarda es el **nombre** de una variable de entorno
(`connection.secret_ref`), nunca su contenido. La sesión vive en el entorno del
servidor. Si alguien roba un respaldo de la base de datos, no se lleva ninguna
credencial tuya.

### 3. Norte se identifica y no evade nada
El conector envía un `User-Agent` que dice quién es. Espera mínimo 2 segundos entre
peticiones. Si la plataforma responde 401, 403 o redirige al login, **se detiene** y
te pide reautorizar. No rota identidades, no simula un navegador, no reintenta a la
fuerza. Si la plataforma no quiere ese tráfico, la respuesta correcta es parar.

### 4. Solo lectura
El conector pide exportaciones de reportes. Nunca crea, modifica ni cancela nada en
la plataforma de origen.

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
- **La sesión expira.** Tendrás que renovarla cada cierto tiempo. Norte te avisa,
  no lo hace solo.

## La alternativa siempre está disponible

Todo lo que hace el Tier 3 lo puedes hacer con **Tier 2**: exportas el reporte desde
el panel, lo subes a Norte, y obtienes exactamente los mismos dashboards. Es más
manual y es completamente seguro. Si tienes dudas, empieza por ahí.

## Cómo activar una conexión Tier 3

1. Entra a **Configuración → Conexiones → Nueva conexión**.
2. Elige la plataforma marcada con el candado naranja (Tier 3).
3. Lee la advertencia y marca la casilla de consentimiento. Ese clic queda registrado
   con fecha, hora y tu usuario en `connection.consent_granted_at`.
4. Pídele a quien administre el servidor que guarde tu sesión en la variable de
   entorno que la pantalla te indica. **No la pegues en ningún campo de la interfaz.**
5. Activa la conexión. La primera sincronización corre en la siguiente pasada del
   worker, o puedes dispararla a mano desde la misma pantalla.

## Cómo desactivarla

Configuración → Conexiones → la conexión → **Desactivar**. Deja de consultarse de
inmediato. Los datos ya ingeridos se quedan: son tuyos. Para borrarlos también,
elimina la conexión, y sus guías y movimientos se van con ella.

## Cómo revocar el acceso de verdad

Desactivar la conexión en Norte detiene a Norte. Para cortar el acceso de raíz,
cierra la sesión desde la plataforma de origen (normalmente "cerrar sesión en todos
los dispositivos") y borra la variable de entorno del servidor. Haz ambas cosas si
sospechas que la credencial se filtró.
