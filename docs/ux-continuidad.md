# Revisión UX: continuidad y comodidad

Fecha: 2026-09-28 · Rama: `ux/continuidad`

Revisión pensada para Osvaldo, el comerciante de contraentrega que usa Master
Data todos los días. Aquí "continuidad" significa que **nunca pierde dónde
estaba**: los filtros sobreviven a cambiar de pantalla y a recargar, el botón
Atrás funciona, después de una acción cae donde espera, lo que tarda muestra su
avance y sobrevive a navegar, los errores dicen qué pasó y qué hacer, y lo
destructivo se confirma. No es un rediseño: se conservaron los colores, las
fuentes, los componentes y los tokens.

Cómo se revisó: la app corrió en local (API con uvicorn + Postgres desechable
en el servidor de casa, web con `next build && next start`). Se cargó un
comerciante con 360 guías a través del flujo real de carga. Se sacaron capturas
con Playwright en escritorio (1366 px) y celular (390 px) antes y después, y se
recorrió el camino diario con un script: entrar → Global → tablero del país →
Órdenes → volver al tablero → Cargar datos. Ninguna página tiene scroll
horizontal a 390 px.

Estado: **Hecho** = arreglado en esta rama, con prueba · **Decisión** = queda
para el dueño · **Anotado** = visto, sin cambio.

## Hallazgos

| # | Problema | Dónde | Arreglo | Estado |
|---|----------|-------|---------|--------|
| 1 | Volver al **Tablero** desde Órdenes (o entrar al día siguiente) lo dejaba en "Máximo" y en la pestaña Dinero: el menú armaba el enlace con la URL de la pantalla actual, y Órdenes no tiene rango. | `components/AppShell.tsx` | `lib/filter-memory.ts`: los últimos rango, plataforma, estados y pestaña usados en un tablero se guardan en el navegador. Los enlaces del menú los llevan y, al abrir un tablero sin filtros en la URL, se restauran (`replace`, así Atrás no se detiene en la versión sin filtro). Un atajo como "Últimos 7 días" se guarda como atajo, así mañana sigue siendo los últimos 7 días. | Hecho |
| 2 | En **Órdenes**, la búsqueda, el estado, las fechas, "solo abiertas", la página y la guía abierta vivían en el estado del componente: Atrás, recargar o ir al tablero y volver los borraba todos. | `app/[country]/orders/page.tsx` | Todo vive en la URL (`buscar`, `estado`, `desde`, `hasta`, `abiertas`, `pagina`, `guia`). Abrir una guía se deshace con Atrás. Cambiar un filtro vuelve a la página 1. | Hecho |
| 3 | Al cambiar de página en Órdenes, la tabla se cambiaba por un esqueleto (salto de diseño) y la página nueva quedaba abierta desde abajo. | `app/[country]/orders/page.tsx` | Las filas anteriores quedan atenuadas mientras carga la página nueva, y la vista sube al inicio de la tabla. | Hecho |
| 4 | Si la sesión vencía, al volver a entrar se perdían los filtros: `?next=` guardaba solo la ruta. Y con sesión activa, `/login?next=…` mandaba siempre a `/global`. | `middleware.ts`, `lib/api.ts` | `loginUrlFor()` guarda ruta + query. El middleware respeta `next` cuando ya hay sesión (siempre dentro del mismo origen, con `safeNextPath`). | Hecho |
| 5 | Los errores sin mensaje del API salían como **"Error 502"** o **"Failed to fetch"** en inglés. Es el caso más común: el servidor gratuito dormido. | `lib/api.ts` | `friendlyErrorMessage()`: frase en español que dice qué pasó y qué hacer ("El servidor está despertando… pulsa Reintentar", "No hay conexión…", "Tu usuario no tiene permiso…"). Un fetch que no sale del navegador se vuelve `ApiError`. | Hecho |
| 6 | **Cargar datos** olvidaba el tipo de reporte y la plataforma: cada día eran tres pasos para la misma carga de Effi. | `app/[country]/cargar/page.tsx` | Arranca en el tipo y la plataforma de la última carga de ese país (`lib/upload-memory.ts`). `?tipo=` en el enlace elige el tipo. | Hecho |
| 7 | Al salir de Cargar datos mientras un archivo se procesaba, la tarjeta "Procesando" desaparecía y nada decía si el archivo había entrado. | `app/[country]/cargar/page.tsx`, `api/routers/ingest.py` | `GET /ingest/jobs?country=` (nuevo filtro). Al volver, los archivos en cola o procesando, y los que fallaron hace menos de 30 min, reaparecen. | Hecho |
| 8 | Mientras el archivo viajaba al servidor no había aviso: se podía soltar otro archivo o cerrar la pestaña y perder la carga. | `app/[country]/cargar/page.tsx` | "Subiendo el archivo… no cierres esta pestaña", zona de carga ocupada, y cerrar la pestaña pide confirmación. | Hecho |
| 9 | El **código de emparejamiento de Effi** desaparecía al recargar o salir de Conexiones; generar otro revocaba el que ya estaba escrito en la extensión. | `components/EffiExtensionPairingPanel.tsx` | El código pendiente se guarda en la pestaña (sessionStorage) hasta que caduca o se usa, con su cuenta regresiva. Se agregó "Copiar código". | Hecho |
| 10 | Tres acciones destructivas no pedían confirmación: desconectar la API de Dropi (el token no se puede volver a ver), desconectar una cuenta con acceso guardado y restablecer el orden del tablero. | `DropiTokenPanel.tsx`, `ConnectionCredentialPanel.tsx`, `DashboardGrid.tsx` | Pasan por `ConfirmDialog`, que dice qué se pierde. El botón de Dropi ya no dice "Guardando…" mientras prueba. | Hecho |
| 11 | La banda de la tarjeta decía **"Vista parcial: falta movements"** (código crudo del SQL). | `components/WidgetRenderer.tsx` (texto de `migrations/003`) | Se reescribe con las palabras del glosario ("falta movimientos de dinero") y enlace "Cargar movimientos de dinero" que abre Cargar datos en ese tipo. | Hecho |
| 12 | La tarjeta bloqueada llevaba a `/settings`, donde ya no se carga ni se conecta nada, y su enlace estaba dentro de un `aria-hidden`. | `components/WidgetRenderer.tsx` | Lleva a Cargar datos en el tipo que falta (o a Conexiones). Solo el fondo borroso queda oculto a lectores de pantalla. | Hecho |
| 13 | Tablero que no cargaba: "Revisa que la API esté corriendo" (mensaje para programadores) y sin botón para reintentar. Pestaña vacía y país inactivo decían a dónde ir pero sin enlace (y citaban "Configuración → Conexiones", que ya no existe así). | `app/[country]/page.tsx` | `ErrorState` con el mensaje real y Reintentar. Los vacíos ofrecen botones: Cargar datos, Ir a Conexiones, Ir a Configuración. | Hecho |
| 14 | En Órdenes sin guías, el vacío explicaba pero no ofrecía el paso siguiente. | `app/[country]/orders/page.tsx` | Botón "Cargar guías". | Hecho |
| 15 | "Resumen del día" mostraba **"Falta GEMINI_API_KEY o AI_ENABLED"** y un chip "Modo degradado". | `app/global/page.tsx`, `components/CopilotPanel.tsx` | `briefSummary()` en `lib/glossary.ts`: "El resumen automático todavía no está activado… tus tableros funcionan igual". Chip: "Sin resumen automático". | Hecho |
| 16 | En el celular los filtros se esconden tras un botón "Filtros" que no decía qué estaba aplicado. | `components/AppShell.tsx` | El botón muestra el rango ("Filtros · Últimos 7 días") y "+N" si hay plataforma o estados filtrados. | Hecho |
| 17 | Hay **dos pantallas llamadas "Global"**: `/global` (donde se cae al entrar, sin entrada en el menú) y `/organizacion` (la entrada "Global" del menú). Al entrar, nada del menú queda marcado. | `app/global`, `app/organizacion`, `AppShell` | Ver decisiones. | Decisión |
| 18 | Sin rango en la URL, el tablero arranca en "Máximo" (todo el histórico) la primera vez. | `lib/date-range.tsx` | Ver decisiones. | Decisión |
| 19 | La salud de conexiones en Global muestra "Con tu usuario y contraseña (riesgo alto)" para una conexión **por archivo** de Effi: la etiqueta sale del `tier` de la plataforma, no del modo de la conexión, y `/config/connections` no devuelve `source_mode`. | `app/global/page.tsx` | Requiere exponer `source_mode` en `Connection`. | Anotado |
| 20 | Formatos de número distintos en la misma pantalla: la franja de decisiones dice "20,590,800 COP" (texto armado en el API) y las tarjetas "$ 20.590.800". | `DecisionStrip` / API | Formatear en el API con las reglas del país, o mandar el número aparte. | Anotado |
| 21 | En Conexiones la sección Pauta aparece primero y Effi/Dropi (lo que Osvaldo usa) más abajo. | `app/connections/page.tsx` | Ordenar por "lo que ya usas" o por país. | Anotado |
| 22 | `next dev` no hidrata: la CSP (`script-src` con nonce y sin `'unsafe-eval'`) bloquea el `eval` que usa React en desarrollo; el formulario de entrada se envía como GET. Producción no se ve afectada. | `middleware.ts` | Agregar `'unsafe-eval'` solo cuando `NODE_ENV === "development"`. No se tocó por no ser UX. | Anotado |

## Decisiones para el dueño

1. **Unificar "Global"** (#17). Recomendación: una sola pantalla "Global".
   Que el menú apunte a la que se ve al entrar y que la otra sea una sección de
   ella (o que `/global` redirija a `/organizacion` para quien administra la
   organización). Hoy quien entra ve una pantalla que el menú no marca y, al
   tocar "Global", llega a otra distinta con el mismo título.
2. **Rango por defecto la primera vez** (#18). Recomendación: "Este mes" en
   vez de "Máximo". Con la memoria de filtros (#1), el defecto solo aplica la
   primera vez en cada navegador; "Máximo" mezcla meses viejos con el día a día
   y hace lento el primer tablero de una cuenta con historia larga.
3. **Memoria de filtros por navegador y no por usuario.** Se guarda en
   `localStorage`: en otro equipo Osvaldo empieza de cero. Si lo usa desde
   varios equipos, conviene guardarla en el servidor (preferencias del
   usuario, como ya se hace con el orden del tablero). Recomendación: dejarla
   así hasta que alguien lo pida.

## Pruebas agregadas

- `tests/unit/api-errors.test.ts`: mensajes de error legibles y el abort intacto.
- `tests/unit/login-redirect.test.ts`: `next` con query; el middleware lo respeta y sigue sin salir del origen.
- `tests/unit/filter-memory.test.tsx`: atajos que se recalculan, rango a mano, pestaña, datos corruptos, restauración al llegar.
- `tests/unit/order-filters.test.ts`: lectura y escritura de filtros de Órdenes en la URL.
- `tests/unit/upload-memory.test.ts`: tipo y plataforma recordados; cargas que se retoman.
- `tests/unit/effi-extension-pairing.test.tsx`: el código sobrevive al desmontar; el caducado no vuelve.
- `tests/unit/dashboard-grid.test.tsx`, `tests/unit/dropi-token-panel.test.tsx`: confirmación antes de borrar.
- `tests/unit/widget-renderer.test.tsx`: textos y enlaces de tarjetas bloqueadas y parciales.
- `tests/unit/brief-summary.test.ts`: el resumen nunca muestra razones técnicas.
- `tests/test_upload_by_platform.py`: `GET /ingest/jobs?country=`.
