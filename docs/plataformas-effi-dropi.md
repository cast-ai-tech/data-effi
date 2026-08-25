# Effi y Dropi lado a lado: la plataforma como dimensión

Referencia técnica de las migraciones **040** y **041**, del parámetro `platform` en
la API, del selector en la interfaz y del informe diario imprimible. Si buscas la guía
para usuarios, está en el README, sección "Si usas Effi Y Dropi".

---

## 1. De dónde sale la plataforma

Cada guía (`core.shipment`) pertenece a una conexión (`connection_id`), y cada conexión
pertenece a una plataforma del catálogo (`core.connection.platform_code` → `core.platform`).
Por eso **la plataforma no es una columna nueva**: es un join que ninguna vista hacía.

`stg.v_shipment_economics` gana dos columnas al final, `platform_code` y `platform_name`
(al final porque `CREATE OR REPLACE VIEW` no reordena). Nada más cambia en esa vista.

**Consecuencia práctica:** para que el tablero separe Effi de Dropi, cada plataforma
necesita su propia conexión y cada archivo debe subirse a la conexión correcta. Un
archivo de Dropi subido a la conexión de "Carga manual" cuenta como `manual_xlsx`.

## 2. El filtro: un cuarto parámetro, no un ajuste de sesión

Las trece funciones de rango `mart.f_*` (migraciones 018/020) reciben
`p_platform text DEFAULT NULL`. `NULL` significa "todas", así que todo lo que llamaba
con tres argumentos sigue significando lo mismo. Las versiones de tres argumentos se
borran primero, como hizo 020 al añadir el tercero, para que la llamada no sea ambigua.

El predicado es siempre el mismo, en la misma posición (justo después de la cláusula
de tenant del escaneo de guías):

```sql
AND mart.f_platform_matches(e.connection_id, p_platform)
```

La migración 041 está **generada** a partir de las definiciones vivas de las funciones
(`pg_get_functiondef`), con exactamente esos dos cambios por función. Un diff contra
018–028 muestra solo esas líneas.

**Por qué no un GUC leído por una política RLS.** Filtraría de forma transparente, y de
forma silenciosa: una conexión del pool que olvidara limpiarlo mostraría los números de
Effi bajo un encabezado que dice "Todas". Un parámetro tiene que pasarse, y la API
devuelve cuál pasó.

**El único punto donde el filtro es incompleto, nombrado:** `f_global_summary` resta
pauta. La pauta pertenece a una conexión de anuncios, nunca a Effi ni a Dropi, así que
bajo un filtro de plataforma `ad_spend` es 0 y `contribution` es contribución **antes**
de medios. El endpoint lo documenta.

## 3. La API: `platform` de ida y `platform` de vuelta

Todos los endpoints con rango aceptan `platform=<código>`. El código se valida contra
`core.platform`: uno desconocido responde `422 invalid_platform` con la lista de los
válidos, para que un error de tipeo nunca se ensanche a "todas" en silencio.

`KpiResponse.platform` dice qué se **aplicó**, con el mismo contrato que `date_basis`:

| Respuesta | Significado |
|---|---|
| `"effi"` | La cifra es solo de guías cargadas por Effi. |
| `null` | La cifra mezcla todas las plataformas: no se pidió ninguna, **o este endpoint no puede separarlas**. |

No pueden separar, y lo dicen: `/kpis/daily-contribution`, `/kpis/cohorts`, `/kpis/cs`,
`/kpis/cpa` (vistas sin la plataforma en su grano), `/kpis/carrier-by-zone` y
`/kpis/layout` (sin rango). `/kpis/platforms` lo ignora a propósito: **es** la
comparación entre plataformas, igual que `/kpis/global` ignora el país.

`f_excluded_no_date` también recibe la plataforma, para que el conteo de "guías fuera del
rango por no tener esa fecha" describa las mismas guías que la pantalla muestra.

## 4. Los cinco grupos de estado

Trece estados canónicos son el grano correcto para fusionar archivos y el incorrecto
para una tabla diaria. `core.status_canon.status_group` (migración 045, antes
`display_group`) es la fuente de verdad de la agrupación con las cinco palabras que lee el
operador; **nunca reemplaza al código canónico** (la fila de una guía sigue diciendo "En
oficina"), solo agrupa. Cada canónico cae en exactamente un grupo (columna NOT NULL +
CHECK).

| Grupo | Etiqueta | Estados canónicos | Por qué juntos |
|---|---|---|---|
| `entregada` | Entregado | `delivered` | Effi "Entregada a destino" y Dropi "Entregado" son una columna. |
| `en_transito` | En tránsito | `created` … `out_for_delivery` | Todavía puede entregarse. |
| `novedad` | Novedad | `delivery_issue`, `in_office` | Se detuvo y una llamada aún la rescata. "En oficina" no puede desaparecer en "en tránsito". |
| `devolucion` | Devolución | `returning`, `returned`, `cancelled` | La venta se perdió y el producto volvió (o nunca salió). Decisión del operador: la cancelada cuenta como devolución, así que `% devolución` la incluye. |
| `indemnizacion` | Indemnización | `lost`, `compensated` | La transportadora perdió el paquete: `lost` es la indemnización pendiente, `compensated` ("Indemnizada", nuevo en 045) la pagada. |

`compensated` es terminal y es el único destino al que puede moverse una guía ya
terminal: `core.status_advance` y `merge_shipment` permiten `lost → compensated` y nada
más. Sus alias (`indemnizada`, `indemnizacion`, `guia indemnizada`…) son **candidatos**:
aún no se ha visto un export real con la palabra; `resolve_status` reporta la grafía que
llegue si no coincide.

Espejos: `pipeline/mapping.py::STATUS_GROUPS` y `web/lib/status.ts`. Los tests
`test_status_groups.py` y `status.test.ts` los mantienen iguales; el test Postgres compara
el seed SQL con la copia de Python y verifica que ningún canónico quede sin grupo.

Dónde se ven los grupos: `mart.v_daily_status_by_platform` / `f_daily_status`,
`mart.v_platform_summary` / `f_platform_summary` (columnas `entregada`, `devolucion`,
`en_transito`, `novedad`, `indemnizacion`), `mart.v_orders.status_group` (filtro `group`
en `/orders`) y `mart.v_global_summary` / `f_global_summary` (los cinco conteos al final;
`in_transit` sigue siendo "toda guía abierta").

Alias nuevos (040): `incidencia en ruta` e `incidencia` → `delivery_issue`, más el
vocabulario que Effi ya tenía registrado bajo `dropi` para que una búsqueda por
plataforma lo encuentre.

## 4b. El perfil exacto de Dropi (migración 047, `pipeline/profiles.py`)

Dos archivos reales de Guatemala (2026-08-24) definieron dos perfiles, al lado de los de
Effi:

| Perfil | Archivo | Firma (cabeceras normalizadas) |
|---|---|---|
| `dropi_ordenes` | `ordenes_YYYYMMDD_HHMMSS.xlsx`, 63 columnas | `numero guia`, `estatus`, `transportadora`, `valor de compra en productos`, `total en precios de proveedor` |
| `dropi_cartera` | `historial de cartera-DD-MM-YYYY HH_MM.xlsx`, 9 columnas | `monto previo`, `numero de guia`, `descripcion`, `tipo` |

Órdenes, columna → campo: `ID` → id de orden Dropi; `NÚMERO GUIA` → número del
transportador y **clave de la guía** (una orden cancelada sin guía usa `DROPI-<ID>`);
`FECHA` → fecha de creación; `ESTATUS` → estado; `ÚLTIMO MOVIMIENTO` → detalle;
`TRANSPORTADORA`, `DEPARTAMENTO DESTINO`, `CIUDAD DESTINO`, `TIENDA`; `VALOR DE COMPRA
EN PRODUCTOS` → valor a recaudar; `PRECIO FLETE`, `COSTO DEVOLUCION FLETE`, `TOTAL EN
PRECIOS DE PROVEEDOR` → costo, `COMISION`. `GANANCIA` es derivada (valor − proveedor −
flete − comisión − devolución en 470/470 filas) y no se guarda. Nombre, teléfono,
documento y dirección van cifrados a `core.shipment` y como hash al archivo crudo.
**No hay fecha de entrega en el export**: las entregadas quedan sin `delivered_at`.
`CONTADOR DE INDEMNIZACIONES > 0` marca la guía como `compensated`.

Estados crudos (8, todos reconocidos): ENTREGADO → delivered · DEVOLUCION → returning ·
INCIDENCIA EN RUTA → delivery_issue · EN RUTA → in_transit · RECOLECTADO → picked_up ·
GUIA_GENERADA → created · CANCELADO → cancelled · PREPARADO PARA TRANSPORTADORA →
confirmed.

Cartera: `TIPO` (ENTRADA/SALIDA) y `MONTO` siempre positivo; el concepto vive en las
primeras palabras de `DESCRIPCIÓN`. Dropi liquida **neto**: la "ganancia" y el "cobro de
devolución" ya están dentro de las columnas de la orden, así que van a tipos de
categoría `transfer` (`settlement_in`, `settlement_out`) que ningún KPI suma; el retiro es
`withdrawal` y la garantía `adjustment_in`. **A confirmar con el operador**: que
"DEVOLUCION DE DINERO POR GARANTIA" sea plata nueva y no un reembolso ya contado.

País: el export no lo dice; lo declara la carga (042). Los departamentos son la única pista.

## 5. Las dos respuestas nuevas

### `mart.f_daily_status(p_from, p_to, p_field, p_platform)` / `mart.v_daily_status_by_platform`

Una fila por **día × plataforma**: `shipments`, un conteo por grupo, `cerradas`
(terminales) y tres porcentajes:

- `pct_devolucion_total` = devoluciones / guías del día. Lo que imprime el informe manual.
  Subestima la tasa en los días recientes: una guía en tránsito no puede haberse devuelto.
- `pct_devolucion_cerradas` = devoluciones / cerradas. Lo que se cumple cuando el día madura.
- `pct_entrega_cerradas` = entregadas / cerradas.

`sample_quality = 'muestra_corta'` con menos de 10 cerradas, la misma regla de 021.

`day` **es la fecha elegida** (`f_pick_date`): con `date_field=entrega` la tabla se lee
"entregadas por día de entrega". Una guía sin esa fecha queda fuera y
`excluded_no_date` la cuenta.

### `mart.f_platform_summary(...)` / `mart.v_platform_summary`

Una fila por plataforma con los mismos conteos, más `share_pct` (parte de las guías del
país que entró por esa plataforma, ventana sobre el país) y `first_day` / `last_day`.

Las dos vistas están escritas en SQL completo, **sin llamar a la función**: una vista de
`mart` que llama a una función deja de correr como su dueño y el rol de solo lectura del
copiloto la pierde (cabecera de 023 y test estructural en `test_kpi_date_filters`). El
test de paridad en `test_platform_filter.py` comprueba que vista y función coinciden
columna por columna.

Ambas están en `ALLOWED_VIEWS` del copiloto (`ai/nl2sql.py`) con su descripción.

## 6. La interfaz

- **URL como fuente de verdad.** `?platform=dropi` viaja junto a `from`, `to` y `field`.
  `useRangedApi` lo añade a cada petición y lee `platform` de vuelta en el sobre.
- **Selector** (`components/PlatformPicker.tsx`) en la cabecera, junto al de fechas. Sus
  opciones salen de `/config/connections` (categorías que cargan guías), nunca de una
  lista escrita en el código. Con una sola plataforma y sin filtro activo no se muestra;
  con un filtro que llega por enlace y no tiene conexión, se muestra igual, con una ×.
- **Nota en cada tarjeta** (`DateBasisNote.tsx::usePlatformNote`): si hay plataforma
  elegida y el servidor respondió `platform: null`, franja "Todas las plataformas";
  si respondió, pie "Plataforma · effi". Mismo mecanismo que la nota de fecha.
- **Widgets** (pestaña Logística, `core.widget_catalog`): `platform_split` (barra de
  participación + una línea por plataforma + consolidado) y `daily_status_table` (un
  bloque por plataforma con TOTAL GENERAL). Los días sin guías dentro del rango
  aparecen en cero, no desaparecen.
- **Informe diario** (`/[país]/informe`): la misma tabla y el consolidado, en una página
  con hoja de estilos de impresión. "Guardar en PDF" llama a `window.print()`; no hay
  renderizador en el servidor porque el navegador ya sabe hacer un PDF. El job
  `daily_digest` deja cada mañana una notificación `daily_report` con el enlace y el
  rango de los últimos 14 días hasta ayer (`ai/alerts.py::persist_report_ready`).

## 7. Lo que el informe manual hace y aquí NO se copia

- **"Ventas".** Una guía es una venta cuando se entrega y se cobra. Las columnas dicen
  `shipments`, y el dinero está en `revenue` / `contribution`.
- **Un solo % de devolución.** Ver arriba: viajan los dos y el estimado lleva `~`.
- **Días que faltan sin aviso.** El informe manual salta el 2 y el 9 de agosto en
  silencio. Aquí, dentro de un rango acotado, cada día tiene fila.
- **Formatos de fecha distintos por plataforma.** Dos parsers en el archivo, una fecha en
  la base. `readers.py` normaliza antes de que nada llegue a `core.shipment`.

## 8. Cómo llega un archivo a su plataforma (migración 042)

Antes de 042 la plataforma la decidía la **conexión** elegida en la pantalla de carga,
y una conexión Effi no podía ni crearse sin consentimiento Tier 3, aunque nadie fuera a
usar una sesión. Resultado real: el export de Effi de Distrilatam cargado como
`manual_xlsx`.

**`core.connection.source_mode`** separa *de quién* son los datos (plataforma) de
*cómo llegan*: `file` (alguien sube el export), `session` (el worker replica la sesión
del operador; exige consentimiento), `sheet`, `webhook`, `api`. El trigger
`enforce_tier3_consent` exige consentimiento solo cuando `source_mode = 'session'`, y
`job_sync_tier3` solo toca conexiones `session`. Las filas existentes se reclasifican
por la evidencia que ya tenían (consentimiento → session, `source_url` → sheet, token →
webhook, resto → file).

**`POST /ingest/upload` por país.** Acepta `platform_code` + `country_code` en lugar de
`connection_id`. Busca la conexión `file` activa de (tenant, país, plataforma) y, si no
existe, la crea (`"Dropi · EC · archivo"`). Mismas reglas que la pantalla de conexiones:
plataforma del catálogo y no `planned`, que opere en ese país, país activo, usuario con
acceso al país. Una plataforma global (`manual_xlsx`) usa su única conexión sin país.

**El check.** Para guías y movimientos, cada archivo pasa por `detect_profile` antes de
escribirse. Si el perfil reconocido pertenece a otra plataforma → `422 platform_mismatch`
con `detected_platform_code` y `target_platform_code`. Aplica también por el camino
clásico con `connection_id`: un export de Effi en "Carga manual" ya no entra. Un archivo
sin perfil reconocido no dice de dónde viene y pasa; para eso está `manual_xlsx`.
`/ingest/detect` devuelve `detected_platform_code` / `detected_platform_name` para que
la pantalla preseleccione.

**Pantalla `/[país]/cargar`** (menú lateral → país → "Cargar datos"): paso 1 plataforma
(radio con las que operan en ese país, `guidePlatforms` filtra el catálogo por país;
"con datos" si ya tiene conexión activa), paso 2 tipo, paso 3 archivo. `judgeFile`
(`web/lib/upload-platform.ts`) decide en el navegador: sugerir, aceptar o bloquear con
ambos nombres. El historial se filtra por país. Las plataformas ofrecidas dependen del
tipo de reporte (`platformsForKind`): guías y movimientos → Effi, Dropi, carga manual;
pauta → `ads_manual`; CS → `cs_sheet`. Solo entran plataformas con `auth_type` `file` o
`session` (el export de Effi es un archivo aunque su modo nativo sea sesión).

**No hay "Cargar datos" global.** La entrada del menú se quitó: todo archivo es de un
país y de una plataforma. `/ingest` sigue existiendo solo para reenviar — al país si hay
uno, o a una lista de países si hay varios — para que los enlaces viejos y el botón del
onboarding no se rompan.

## 9. Lo que queda pendiente

- **Guías ya cargadas como `manual_xlsx`.** Las 1.649 guías de EC entraron por la
  conexión de carga manual antes de 042. Re-subir el mismo export como Effi crearía
  duplicados (la clave natural es `(connection_id, tracking_number)`): hay que
  **mover** `core.shipment`, `core.movement` y `raw.load_batch` de la conexión manual
  a la conexión `effi · file`. Es un UPDATE de datos de producción; se hace con el OK
  del operador.
- **Conector Dropi por API.** Hoy Dropi entra por archivo. Un conector con credenciales
  vive en `connectors/dropi/` cuando exista acceso a la API; mientras tanto el catálogo
  lo dice en `setup_hint`.
- **Perfil exacto de Dropi.** `pipeline/profiles.py` reconoce el export de Effi por sus
  encabezados. El de Dropi entra por el mapeo genérico de columnas (040 añade
  `total de la orden`, `precio flete`, `departamento destino`). Con un export real a la
  mano se escribe el perfil y desaparece la ambigüedad.
