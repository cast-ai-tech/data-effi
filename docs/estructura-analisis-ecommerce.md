# Estructura de análisis: ecommerce COD

Este documento define **qué mide Norte y por qué**. Es la referencia de negocio
detrás de cada vista `mart.*`. Si un número del dashboard no te cuadra, aquí está la
definición exacta que lo produjo.

---

## 1. El problema del contraentrega

En un ecommerce con pago anticipado, una venta es una venta: el dinero entra cuando
el cliente compra. En **contraentrega (COD)** una venta es apenas una promesa. Entre
el clic y el dinero pasan tres cosas que pueden matarla:

1. El cliente no confirma el pedido (o se arrepiente).
2. La transportadora no logra entregar.
3. El cliente rechaza el paquete en la puerta.

Por eso **ninguna métrica de Norte usa "ventas" como numerador principal**. La unidad
de análisis es la **guía** y su desenlace.

## 2. La unidad de análisis: la guía

Cada fila de `core.shipment` es una guía: un despacho con un número de rastreo, una
transportadora, un destino, un producto y un valor a recaudar.

**Clave natural:** `(connection_id, tracking_number)`. La misma guía puede aparecer en
diez reportes distintos; siempre es una sola fila.

## 3. La escalera de estados

Todo estado crudo que llega ("ENTREGADO", "en reparto", "Devolución en tránsito") se
traduce a un código canónico con un `sort_order`:

| Orden | Código | Terminal | Significa |
|-------|--------|----------|-----------|
| 10 | `created` | no | Guía generada |
| 20 | `confirmed` | no | Pedido confirmado con el cliente |
| 30 | `picked_up` | no | Recogida por la transportadora |
| 40 | `in_transit` | no | En tránsito |
| 50 | `out_for_delivery` | no | En reparto |
| 55 | `delivery_issue` | no | Novedad |
| 60 | `delivered` | **sí** | Entregada y recaudada |
| 70 | `returning` | no | En devolución |
| 80 | `returned` | **sí** | Devuelta al origen |
| 90 | `cancelled` | **sí** | Cancelada |
| 95 | `lost` | **sí** | Extraviada |

**Regla de oro:** el estado solo avanza. Un archivo viejo que dice "en tránsito" nunca
puede des-entregar una guía ya marcada como entregada. Los estados terminales quedan
congelados. Esta regla vive en `merge_shipment()` y se replica en el `ON CONFLICT` de
`PostgresStore`.

## 4. Las dos tasas de entrega (y por qué son distintas)

Norte reporta dos, y confundirlas es el error más común del sector:

- **Efectividad terminal** = `entregadas / (entregadas + devueltas + muertas)`
  Solo cuenta guías que ya se resolvieron. Es la tasa **real** de la operación.
- **Efectividad sobre despachadas** = `entregadas / total despachadas`
  Incluye las que siguen en tránsito. Siempre es menor y **siempre engaña hacia abajo**
  en cohortes recientes.

En una cohorte de ayer la segunda dará 5%, y no significa nada: el 95% restante aún
está viajando. Por eso existe la maduración.

## 5. Maduración de cohortes

Una **cohorte** es el conjunto de guías creadas el mismo día. Su tasa de entrega sube
con los días hasta estabilizarse.

`workspace_country.maturation_days` (default 21) define cuántos días necesita una
cohorte antes de que su número sea confiable. El worker calcula el **p90 real** de
días-a-entrega de tu operación y propone un valor en
`maturation_days_suggested` — **propone, no aplica**. Cambiar cómo se mide el negocio
es una decisión de negocio.

`mart.v_cohort_maturation` marca cada punto con `is_observable` (¿ya pasaron esos
días?) e `is_mature` (¿la cohorte completa ya maduró?). El frontend nunca dibuja una
curva sin distinguirlas.

## 6. La cascada de contribución

Norte no habla de "utilidad". Habla de **contribución**: lo que queda de cada guía
después de sus costos directos.

```
  Valor recaudado          (solo de guías entregadas)
- Flete de envío           (se paga aunque no se entregue)
- Flete de devolución      (la devolución cuesta dos veces)
- Costo del producto
- Comisiones de plataforma
+/- Ajustes
- Pauta                    (si hay conexión de ads; si no, se marca y se avisa)
= CONTRIBUCIÓN
```

La devolución es el asesino silencioso del COD: pagas el flete de ida, el de vuelta, y
no recaudas nada. Una guía devuelta no es "una venta perdida", es **una pérdida real
en efectivo**.

### De dónde sale cada cifra

`stg.v_shipment_economics` resuelve cada monto con esta precedencia:

1. **Movimientos contabilizados** (`core.movement`) — si existen, mandan.
2. **Valores en la guía** (`freight_cost`, `product_cost`, …).
3. **Costo del catálogo** (`product.unit_cost × cantidad`), solo si fue entregada.

Los movimientos ganan porque son el dinero real, no el estimado.

## 7. Antigüedad (aging)

Guías abiertas por tramo de días desde su creación:

| Tramo | Lectura |
|-------|---------|
| 0–3 | Normal |
| 4–7 | Normal |
| 8–12 | Vigilar |
| **13–20** | **Zona de pérdida** — probabilidad de entrega cae fuerte |
| 21+ | Prácticamente perdida |

`mart.v_aging` reporta también el **valor en riesgo**: la suma declarada de lo que
está atascado. No es un conteo, es plata.

## 8. Semáforo geográfico

Por ciudad, sobre guías terminales (mínimo 10 para tener señal):

- **Verde**: ≥ 80% de entrega
- **Amarillo**: 65% – 80%
- **Rojo**: < 65%
- **Sin datos**: menos de 10 guías resueltas

Un rojo con volumen alto es la decisión más rentable que vas a tomar en la semana:
subir el precio ahí, exigir confirmación previa, o dejar de despachar a esa ciudad.

## 9. CPA y ROAS

Solo existen si hay conexión de pauta. Y se reportan **dos** CPA:

- **CPA sobre despachadas** = `pauta / guías generadas` — lo que ves en el Business
  Manager. Optimista.
- **CPA sobre entregadas** = `pauta / guías entregadas` — lo que realmente pagaste por
  cada venta cobrada. **Este es el que manda.**

Con 70% de efectividad, tu CPA real es 43% más alto que el que reporta Meta. Norte
muestra los dos lado a lado para que la brecha sea imposible de ignorar.

## 10. Degradación honesta

Cuando falta una fuente, Norte **no esconde el widget**: lo muestra bloqueado, con
candado, diciendo exactamente qué conector falta y con un botón para conectarlo.

Un dashboard que oculta lo que no sabe te hace creer que lo viste todo.
`mart.v_country_dashboard_layout` resuelve el estado de cada widget
(`available` / `degraded` / `blocked`) en la base de datos. El frontend solo lo pinta.

Y cuando el número existe pero es parcial —contribución sin pauta, por ejemplo— la
vista marca `ad_spend_missing = true` y el widget lo dice en voz alta.

## 11. Lo que Norte nunca guarda

- Teléfonos, cédulas, direcciones ni nombres de clientes finales.
- Solo `customer_hash`: SHA-256 con salt por tenant, irreversible.

Sirve para contar clientes recurrentes y detectar rechazadores seriales. No sirve —a
propósito— para contactar a nadie.
