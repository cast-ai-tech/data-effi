# Arquitectura multi-país y conectores

Cómo Data Effi soporta varios países, varias plataformas y varias tiendas sin que el
código sepa nada de Colombia en particular.

---

## 1. Principio: el país es dato, no código

En ningún archivo del frontend ni del backend aparece `COP`, `$`, `dd/MM/yyyy` ni
`Departamento`. Todo eso vive en `core.country`:

| Columna | Para qué |
|---------|----------|
| `currency_code`, `currency_symbol` | Formateo de dinero |
| `decimal_places` | COP y CLP usan 0; MXN, PEN, USD usan 2 |
| `thousands_sep`, `decimal_sep` | `1.234.567` en CO vs `1,234.56` en MX |
| `date_format` | `dd/MM/yyyy` casi siempre, `dd-MM-yyyy` en CL |
| `timezone` | Cierre del día — un despacho de las 11pm en Bogotá no es del día siguiente |
| `geo_level1_label` | "Departamento" en CO/PE/GT, "Estado" en MX, "Provincia" en EC/PA, "Región" en CL |
| `locale` | Ordenamiento y textos |

Agregar un país es una fila en esta tabla más las plataformas disponibles en él. Cero
código.

Países precargados: **CO, MX, PE, EC, CL, PA, GT**.

## 2. Las tres capas de alcance

```
Tenant  ─── el negocio completo (multi-tenant: tenant_id en absolutamente todo)
  └── Country  ─── un país donde opera (core.workspace_country)
        └── Store  ─── una tienda/marca dentro de ese país (core.store)
              └── Connection  ─── una fuente de datos concreta
```

Un tenant puede tener 3 países, 2 tiendas en uno de ellos, y 5 conexiones en total.
Cada guía sabe a qué país y a qué tienda pertenece.

**Aislamiento:** cada consulta filtra por el tenant del JWT. Además, todas las vistas
`mart.*` filtran solas por `core.current_tenant_id()`, que lee la variable de sesión
`norte.tenant_id`. Si esa variable no está puesta, las vistas devuelven **cero filas**.
Falla cerrado, no abierto. En la fase de hardening se suma Row-Level Security como
segunda muralla.

## 3. Conectores por tier

Ver `docs/tier3-politica.md` para la política completa. Resumen:

| Tier | Mecanismo | Consentimiento | Plataformas |
|------|-----------|----------------|-------------|
| 1 | API oficial | No | Shopify, Meta Ads, TikTok Ads, Google Ads |
| 2 | Archivo (subida o buzón) | No | Dropi, carga manual, hoja de CS |
| 3 | Sesión del usuario | **Obligatorio** | Effi |

`core.platform.tier` y `core.platform.requires_consent` gobiernan esto. Un trigger en
la base de datos impide crear una conexión Tier 3 sin `consent_granted_at`.

## 4. Dominios de datos

Cada plataforma declara qué aporta (`core.platform.data_domains`):

- `shipments` — guías, estados, destinos
- `movements` — dinero recaudado y costos
- `ads` — inversión en pauta
- `cs` — confirmaciones de servicio al cliente
- `catalog` — productos y costos

Esto es lo que hace posible la degradación honesta: un widget de CPA requiere
`shipments` **y** `ads`. Sin conexión de ads, `mart.v_country_dashboard_layout` lo
devuelve como `blocked` con su mensaje. El frontend no decide, pinta.

Tres estados posibles:

| Estado | Cuándo |
|--------|--------|
| `available` | Todos los dominios requeridos conectados y con datos |
| `degraded` | Conectado pero aún sin datos, o falta un dominio opcional |
| `blocked` | Un dominio requerido no tiene conexión activa |

## 5. Un solo camino de ingesta

Da igual de dónde venga el archivo:

```
Subida manual  ┐
Buzón de correo├──► bytes ──► IngestEngine ──► Store ──► core.*
Fetch tier 3   ┘                  │
                                  └── content_hash = SHA-256(bytes)
```

**Nada tiene una vía privilegiada.** Un fetch automático pasa por el mismo hash, la
misma idempotencia y las mismas reglas de fusión que una subida manual. Si el mismo
reporte llega por correo y por fetch, se ingiere una sola vez.

## 6. Resolución de dimensiones

Los nombres llegan escritos de cualquier forma. Data Effi los resuelve con *get-or-create*
sobre una clave normalizada (minúsculas, sin tildes, espacios colapsados):

| Dimensión | Clave |
|-----------|-------|
| Transportadora | `(tenant, país, nombre_normalizado)` |
| Geografía | `(tenant, país, nivel1_norm, ciudad_normalizada)` |
| Producto | `(tenant, nombre_normalizado)` + tabla `product_alias` |
| Proveedor | `(tenant, nombre_normalizado)` |

`Bogotá`, `BOGOTA` y `bogota` son la misma ciudad. `core.normalize_text()` en SQL y
`normalize_text()` en Python producen la misma clave — hay un test que lo verifica.

`core.product_alias` existe porque el mismo producto llega como "Faja Reductora",
"FAJA REDUCTORA X2" y "faja-reductora": todos apuntan a un producto canónico.

## 7. Movimientos huérfanos

Los movimientos de dinero suelen llegar **antes** que la guía correspondiente (el
recaudo se contabiliza el día del pago; el reporte de guías se exporta semanal).

Un movimiento sin guía conocida se guarda con `shipment_id = NULL`. El job
`relink_orphans()` los liga cuando la guía aparece. No se descartan nunca: descartar
un movimiento es perder plata registrada.

## 8. Moneda y FX

Cada guía guarda su moneda local. Los dashboards por país trabajan siempre en moneda
local — es la que el operador entiende.

Solo la vista global (`mart.v_global_summary`) convierte a USD, usando la última tasa
conocida de `core.fx_rate`. Si no hay tasa, marca `fx_missing = true` y muestra el
número local en vez de inventar una conversión.

El worker refresca las tasas a diario desde una fuente configurable por entorno, con
respaldo en la última tasa conocida.

## 9. Idempotencia en todos lados

| Entidad | Clave de idempotencia |
|---------|----------------------|
| Carga de archivo | `(tenant, connection, sha256(bytes))` |
| Guía | `(connection, tracking_number)` |
| Movimiento | `(connection, dedupe_key)` |
| Pauta | `(connection, dedupe_key)` |
| Job del worker | Advisory lock de PostgreSQL |

Subir el mismo archivo dos veces produce cero duplicados. Dos workers corriendo a la
vez no duplican trabajo. Dos usuarios subiendo el mismo archivo al mismo tiempo
producen una sola carga efectiva: uno gana la restricción `UNIQUE`, el otro recibe
"ya estaba cargado", que es la respuesta correcta y no un error.
