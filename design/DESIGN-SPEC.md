# Norte — Especificación de diseño

Fuente: `Norte.dc.html` (prototipo dark-mode BI para ecommerce COD en LATAM).
Destino: Next.js + Tailwind. Todos los valores son literales del prototipo.

---

## 1. Tokens de color

### Superficies y fondos

| Token | Hex | Uso exacto |
|---|---|---|
| `bg-page` | `#0B0E14` | `body`, contenedor raíz, topbar (56px), fondo interior del marco móvil |
| `bg-sidebar` | `#0D1017` | Sidebar izquierdo y panel Copiloto IA (slide-over) |
| `bg-surface` | `#12161F` | **Todas** las cards/paneles/tablas, contenedor del selector de rango, chips de sugerencia, input del copiloto, botón móvil inactivo |
| `bg-surface-sunken` | `#0F131A` | Filas internas de Configuración, país no seleccionado en onboarding, caja "Impacto estimado" (móvil), pill del buzón de ingesta, aviso "Bot IA no conectado" |
| `bg-track` | `#1B212C` | Track (fondo) de todas las micro-barras y barras de progreso dentro de celdas |
| `bg-track-alt` | `#181D27` | Track de la barra de progreso del wizard de onboarding |
| `bg-range-active` | `#1E2530` | Botón activo del selector de rango de fechas |
| `bg-phone-bezel` | `#1A1E27` | Bisel de 10px y notch del mockup de iPhone |
| `overlay-lock` | `rgba(11,14,20,0.55)` / `rgba(11,14,20,0.60)` | Velo sobre widget bloqueado (0.55 en dashboard país, 0.60 en la galería de estados) |

### Bordes

| Token | Valor | Uso |
|---|---|---|
| `border-card` | `rgba(255,255,255,0.07)` | Borde por defecto de cards y paneles (36 usos) |
| `border-strong` | `rgba(255,255,255,0.08)` | Sidebar right-border, chips del topbar, borde inferior de la barra de tabs |
| `border-subtle` | `rgba(255,255,255,0.06)` | Header interno de card (separador título/contenido), divisores del sidebar |
| `border-row` | `rgba(255,255,255,0.05)` | `border-top` de cada fila de tabla |
| `border-input` | `rgba(255,255,255,0.10)` | Inputs, chips de pregunta, swatches del design system, botón móvil inactivo |
| `border-panel` | `rgba(255,255,255,0.09)` | `border-left` del panel Copiloto |
| `border-ghost-btn` | `rgba(255,255,255,0.12)` / `0.14` | Botón "Atrás" (0.12) y botón secundario del paso 4 (0.14) |
| `border-dashed-idle` | `1.5px dashed rgba(255,255,255,0.12)` | Tarjetas de país no seleccionadas (onboarding paso 1) |
| `chip-neutral-bg` | `rgba(255,255,255,0.06)` / `0.04` | Badge "Analista" (0.06) y "Solo lectura" (0.04) |

### Texto

| Token | Hex | Uso |
|---|---|---|
| `text-primary` | `#E7EAF0` | Color base del layout, valores KPI, títulos, tab activo, `<b>` dentro de labels de gráfico |
| `text-body` | `#DADEE6` | Cuerpo de los bloques de resumen IA |
| `text-secondary` | `#C4CAD6` | Body del design system, texto dentro de overlays de bloqueo, chips de sugerencia, botón "Atrás"/secundario, badge "Analista" |
| `text-muted` | `#8B93A5` | Labels de KPI, celdas secundarias, leyendas de gráficos, ejes, chip "Sesión", icono de colapso |
| `text-dim` | `#5B6272` | Encabezados de columna, metadatos, subtítulos pequeños, placeholder del input, labels de cuadrante del scatter, curva "Semana 3" |
| `text-faint` | `#4C5364` | Títulos de sección del sidebar (PAÍSES / VISTA PREVIA), países deshabilitados en onboarding |
| `text-nav-idle` | `#9AA1B0` | Ítem de nav inactivo |
| `text-on-accent` | `#06110C` | Texto/iconos sobre fondo acento `#33E5B0` (botones, logo, FAB, badge PRINCIPAL) |

### Semánticos / series

| Token | Hex | Uso |
|---|---|---|
| `accent` (positivo) | `#33E5B0` | Marca, deltas al alza, barra "Contribución" de la cascada, series positivas, aging 0–3d, semáforo ≥75%, chip API, curva Semana 1, links, FAB, botones primarios |
| `accent-hover` | `#5CEFC4` | `a:hover` |
| `accent-2` | `#5FCB9E` | Verde secundario: aging 4–7d, producto secundario en scatter/leyenda |
| `warning` | `#F5A83C` | Estado degradado, semáforo ≥60%, barra "−Flete", aging 8–12d, curva Semana 2, chip "Archivo", canal Bot IA, alerta ATENCIÓN |
| `negative` | `#FF6259` | Barra "−Producto", % problema, aging 13+d, semáforo <60%, devolución, alerta CRÍTICA, validación fallida |
| `negative-soft` | `#F5A0A0` | Texto de detalle del error de validación de archivo |
| `neutral-bar` | `#3A4152` | Barra "Recaudo" (base de la cascada) |
| `neutral-series` | `#5B6272` | Canal "Llamada", barra "Entran en novedad", curva punteada Semana 3 |

Fondos translúcidos de acento (badges/estados):
`rgba(51,229,176, 0.03 | 0.04 | 0.06 | 0.10 | 0.12 | 0.20 | 0.22 | 0.25 | 0.30 | 0.35)`,
`rgba(245,168,60, 0.08 | 0.12 | 0.25 | 0.30)`,
`rgba(255,98,89, 0.06 | 0.12 | 0.25 | 0.30)`,
`rgba(139,147,165, 0.15)` (chip "Sesión").

### Scrollbar
`width/height: 8px`; thumb `rgba(255,255,255,0.12)` radio `8px`; track transparente.

---

## 2. Tipografía

- Familia: **Inter** (Google Fonts, pesos 400/500/600/700/800), fallback `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
- **`font-variant-numeric: tabular-nums` aplicado globalmente en `body`** — crítico: toda cifra en tablas, KPIs y micro-barras alinea columnas.
- `-webkit-font-smoothing: antialiased`.
- No hay `line-height` global; se declara puntualmente: `1.4`, `1.5`, `1.55`, `1.6`, `1.65`.

| Rol | Tamaño | Peso | Letter-spacing | Color |
|---|---|---|---|---|
| Emoji de bandera (onboarding paso 1) | 32px | — | — | — |
| CPD (card destacada del país) | 28px | 800 | `-.02em` | `#33E5B0` |
| Muestra "Display" del design system | 28px | 800 | `-.02em` | primary |
| Bandera en header de país | 28px | — | — | — |
| H1 del wizard de onboarding | 26px | 800 | `-.02em` | primary |
| Número KPI grande | 26px | 800 | `-.02em` | primary |
| Capital en tránsito (card país) | 22px | 800 | `-.02em` | primary |
| Impacto móvil (−$310/semana) | 20px | 800 | — | negative |
| Título de pantalla (Widgets / Design system) | 20px | 800 | — | primary |
| Nombre del país (header) | 20px | 800 | `-.02em` | primary |
| Muestra "Título" | 18px | 700 | — | primary |
| Ciclo de caja ("Tu plata vuelve en…") | 18px | 700 | — | primary |
| KPI móvil | 18px | 800 | — | primary |
| Título de alerta (detalle móvil) | 16px | 700 | lh 1.4 | primary |
| Logo "Norte" / título del topbar | 15px | 700 | `-.01em` | primary |
| Emoji de bandera en nav | 15px | — | `line-height:1` | — |
| Dropzone principal (onboarding) | 15px | 600 | — | primary |
| Header de sección móvil ("Alertas") | 15px | 700 | — | primary |
| Copy IA / cuerpo grande | 14.5px | 400 | lh 1.6–1.65 | `#DADEE6` / primary |
| Título del panel Copiloto | 14.5px | 700 | — | primary |
| Dropzone (pantalla Cargar datos) | 14.5px | 600 | — | primary |
| Subtítulo de onboarding | 14px | 400 | — | `#8B93A5` |
| Cuerpo del resumen IA (global) | 14px | 400 | lh 1.65 | `#DADEE6` |
| Muestra "Cuerpo" | 14px | 500 | — | `#C4CAD6` |
| Subtítulo de pantalla | 13.5px | 400 | — | `#8B93A5` |
| Tab de país (activo / inactivo) | 13.5px | 700 / 500 | — | primary / `#5B6272` |
| Celda de tabla destacada (país, monto) | 13.5px | 600–700 | — | primary |
| Nombre de plataforma (onboarding paso 2) | 13.5px | 600 | — | primary |
| CTA móvil | 13.5px | 700 | — | `#06110C` |
| Título de card / header de tabla | 13px | 700 | — | primary |
| Celda de tabla normal | 13px | 400 | — | primary / `#8B93A5` |
| Ítem de nav (activo / inactivo) | 13px | 600 / 500 | — | `#33E5B0` / `#9AA1B0` |
| Botones del wizard | 13px | 700 / 600 | — | `#06110C` / `#8B93A5` |
| Texto de alerta (copiloto / móvil) | 13px | 400 | lh 1.4–1.5 | primary |
| Selector de tienda (topbar) | 12.5px | 400 | — | `#C4CAD6` |
| Barra de confirmación por canal | 12.5px | 400 / 700 | — | primary |
| Label de KPI | 12px | 600 | — | `#8B93A5` |
| Delta de KPI (▲/▼) | 12px | 700 | — | accent / negative |
| Pill de sync + botón de rango | 12px | 700 / 500 | — | `#33E5B0` / `#8B93A5` |
| Botón de acción en alerta | 12px | 700 | — | color de la severidad |
| Muestra "Metadato" | 12px | 400 | — | `#8B93A5` |
| Etiqueta uppercase del resumen IA | 13px | 700 | `.04em` | `#33E5B0` |
| Chip de sugerencia (copiloto) | 11.5px | 400 | — | `#C4CAD6` |
| Botón de vista móvil | 11.5px | 700 / 600 | — | `#06110C` / `#8B93A5` |
| Label del embudo de novedades | 11.5px | 400 | — | color de la serie |
| Label de eje / leyenda / valor de micro-barra | 11px | 400 | — | `#8B93A5` |
| Encabezado de columna de tabla | 11px | 700 | `.04em` | `#5B6272` + `uppercase` |
| Etiqueta de estado de widget | 11px | 700 | `.05em` | accent / warning / negative + `uppercase` |
| Encabezado de sección (design system, copiloto) | 11px | 700 | `.05em` | `#5B6272` + `uppercase` |
| Badge de rol (Dueño / Analista / Solo lectura) | 11px | 700 | — | según rol |
| Etiqueta de severidad de alerta | 10.5px | 700 | — | color de severidad |
| Título de sección del sidebar | 10.5px | 700 | `.08em` | `#4C5364` + `uppercase` |
| Leyenda del ciclo de caja | 10.5px | 400 | — | `#5B6272` |
| Chip API / Archivo / Sesión | 10px | 700 | — | color de la variante |
| Etiquetas de cuadrante (SVG `<text>`) | 10px | 400 | — | `#5B6272` |
| Badge "PRINCIPAL" | 9px | 700 | — | `#06110C` |

---

## 3. Espaciado, radios y sombras

**Escala declarada en el propio design system:** `4 / 8 / 12 / 16 / 20 / 24 / 32 px`; radios `8–12px`.

### Padding

| Contexto | Valor |
|---|---|
| Contenedor de pantalla | `24px` (Global, País, Cargar datos, Configuración) · `32px 24px` (Widgets, Design system) · `60px 24px` (Onboarding) · `36px 20px` (Móvil) |
| Card KPI | `18px` |
| Card estándar | `20px` |
| Card de cascada P&L | `22px` |
| Card de calibración (paso 4) | `26px` |
| Header interno de card | `16px 20px` |
| Fila de encabezado de tabla | `10px 20px` |
| Fila de datos de tabla | `12px 20px` · `13px 20px` (transportadoras) · `14px 20px` (ranking de países) |
| Fila de archivo (upload) | `14px 18px` |
| Fila de Configuración | `10px 14px` |
| Item de nav | `9px 10px` |
| Header del sidebar | `20px 18px` |
| Footer del sidebar | `12px` |
| Header del copiloto | `18px 20px` |
| Cuerpo del copiloto | `20px` |
| Card de alerta (copiloto) | `14px` |
| Card de alerta (móvil) | `12px 14px` |
| Topbar | `0 24px`, altura fija `56px` |
| Chip de topbar | `7px 12px`; contenedor del selector de rango `3px` |
| Botón de rango | `6px 12px` |
| Chip API / Archivo / Sesión | `3px 7px` (onboarding) · `3px 8px` (design system, badges de rol) |
| Botón primario | `10px 26px` (wizard) · `9px 16px` (calibración) · `7px 16px` / `6px 14px` (overlay bloqueado) |
| Botón secundario | `10px 22px` |
| Dropzone | `56px 20px` (onboarding) · `44px` (Cargar datos) |
| Tarjeta de país (onboarding) | `22px 12px` |
| Tarjeta de plataforma | `14px` |
| Contenido móvil | `16px` |

### Gaps
`2px` (lista de nav) · `4px` (tabs) · `5–6px` (icono+texto) · `8px` (el más común: valor+delta de KPI, listas verticales) · `9–10px` (listas de cards, nav item) · `12px` · `14px` (grid de KPIs global, tarjetas de país) · `16px` (grid de cards de país, aging) · `18px` (grid de estados de widget) · `20px` (columnas de pantalla, secciones) · `26px` (barras de la cascada) · `28px` (secciones del design system).

### Border-radius

| Valor | Uso |
|---|---|
| `4px` | Micro-barras y sus tracks; barras de la cascada (`4px 4px 0 0` cuando nacen del piso) |
| `5px` | Chips API/Archivo/Sesión, badges de rol, barras del embudo |
| `6px` | Botón de rango |
| `7px` | Logo del sidebar, botones del overlay, botones de acción de alerta, aviso inline 🔒 |
| `8px` | Nav item, chips del topbar, botones, filas de Configuración, swatches, alerta móvil, scrollbar |
| `9px` | Icono cuadrado 34px, input del copiloto, botón enviar, CTA móvil |
| `10px` | Cards secundarias, tarjetas de plataforma, filas de archivo, cards de alerta, KPI móvil |
| `12px` | **Radio canónico de card** |
| `14px` | Card de calibración, dropzone de Cargar datos |
| `16px` | Dropzone de onboarding, FAB, pills de sugerencia y botones móviles |
| `44px` | Marco del teléfono; notch `0 0 14px 14px` |
| `50%` | Puntos de estado (6–8px) |

### Sombras

| Uso | Valor |
|---|---|
| FAB del copiloto | `0 8px 24px rgba(51,229,176,0.35)` |
| Panel Copiloto (slide-over) | `-20px 0 40px rgba(0,0,0,0.35)` |
| Mockup de teléfono | `0 30px 60px rgba(0,0,0,0.5)` |

Fuera de esos tres, **no hay sombras**: la jerarquía se construye sólo con fondo + borde de 1px.

### Transiciones / animación
- Sidebar: `width .15s ease`.
- Barra de progreso del wizard: `width .25s`.
- Keyframe `pulse` (`0%,100%{opacity:1} 50%{opacity:.4}`) declarado pero **no aplicado** en el markup.

---

## 4. Inventario de componentes

### 4.1 Sidebar
- Ancho: **224px expandido / 64px colapsado** (`transition: width .15s ease`), `flex-shrink:0`, fondo `#0D1017`, `border-right:1px solid rgba(255,255,255,0.08)`.
- **Header** (`20px 18px`, `border-bottom:1px solid rgba(255,255,255,0.06)`): cuadrado `28×28`, radio `7px`, fondo `#33E5B0`, letra "N" 14px/800 color `#06110C`; junto a él "Norte" 15px/700/`-.01em` (se oculta al colapsar).
- **Lista**: `flex:1; overflow-y:auto; padding:14px 10px; gap:2px`.
- **Nav item**
  - Base: `display:flex; align-items:center; gap:10px; padding:9px 10px; border-radius:8px; font-size:13px; cursor:pointer`
  - **Activo**: `font-weight:600; background:rgba(51,229,176,0.10); color:#33E5B0`
  - **Inactivo**: `font-weight:500; color:#9AA1B0` (sin fondo)
  - Iconos SVG 16×16, `stroke:currentColor; stroke-width:1.4`
- **Títulos de grupo**: 10.5px/700/`.08em`/uppercase/`#4C5364`; padding `14px 10px 6px` (primero) y `16px 10px 6px` (segundo). Se ocultan al colapsar.
- Orden: `Global` → grupo **Países** (🇪🇨 Ecuador, 🇨🇴 Colombia, 🇬🇹 Guatemala) → `Cargar datos` → `Copiloto IA` → `Configuración` → grupo **Vista previa** (① Onboarding, ▦ Estados de widget, ▯ Móvil, ◆ Sistema de diseño).
- **Footer** (`padding:12px`, `border-top:1px solid rgba(255,255,255,0.06)`): botón de colapso de 32px de alto, radio 8px, color `#8B93A5`, chevron 15px que rota 180° al colapsar.

### 4.2 Topbar
- Alto `56px`, `padding:0 24px`, fondo `#0B0E14`, `border-bottom:1px solid rgba(255,255,255,0.07)`, `justify-content:space-between`.
- Izquierda: título de página 15px/700/`-.01em`.
- Derecha (`gap:10px`):
  1. **Selector de rango** — contenedor `#12161F`, borde `rgba(255,255,255,0.08)`, radio `8px`, `padding:3px`, `gap:2px`. Botones `padding:6px 12px; border-radius:6px; font-size:12px`. Activo → `background:#1E2530; color:#E7EAF0; font-weight:700`. Inactivo → sin fondo, `color:#8B93A5; font-weight:500`. Opciones: `Hoy · 7d · 30d · Cohorte` (default `30d`).
  2. **Selector de tienda** — `padding:7px 12px`, `#12161F`, borde `rgba(255,255,255,0.08)`, radio 8px, 12.5px `#C4CAD6`, chevron 10px. Texto: "Tienda Principal".
  3. **Pill de sync-health** — `padding:7px 12px`, fondo `rgba(51,229,176,0.10)`, borde `rgba(51,229,176,0.25)`, radio 8px, 12px/600 `#33E5B0`, punto circular 6px `#33E5B0`. Texto: "Sincronizado hace 12 min".
- Visible sólo en: global, country, widgets, upload, settings.

### 4.3 Card KPI (con sparkline y delta)
```
background:#12161F; border:1px solid rgba(255,255,255,0.07); border-radius:12px; padding:18px
├ label      12px/600 #8B93A5, margin-bottom:10px
├ fila       display:flex; align-items:baseline; gap:8px
│  ├ valor   26px/800/-.02em #E7EAF0
│  └ delta   12px/700  (#33E5B0 si ▲, #FF6259 si ▼)
└ sparkline  <svg width=120 height=32 viewBox="0 0 120 32" style="margin-top:10px;display:block">
             <polyline fill="none" stroke="#33E5B0" stroke-width="1.6"/>
```
- Variante ancha (card CPD del país): `viewBox="0 0 260 40"`, `height:40`, `stroke-width:1.8`.
- Variante widget "disponible": `viewBox="0 0 200 34"`, `stroke-width:1.6`.
- El delta ▼ de "Capital en tránsito" se pinta **verde**: el color codifica *bueno/malo*, no la dirección de la flecha.

### 4.4 Tabla de datos con micro-barras en celda
```
contenedor: #12161F / border 1px rgba(255,255,255,0.07) / radius 12px / overflow:hidden
├ header de card:       padding 16px 20px · 13px/700 · border-bottom rgba(255,255,255,0.06)
├ fila de encabezados:  grid · padding 10px 20px · 11px/700/.04em/uppercase/#5B6272
└ filas:                grid · padding 12–14px 20px · align-items:center · border-top rgba(255,255,255,0.05)
```
- **Micro-barra en celda**: track `height:6px; background:#1B212C; border-radius:4px; overflow:hidden`; ancho fijo `70px` (transportadoras) o `100px` (ranking de países); fill `height:100%; width:{pct}%; border-radius:4px` con color semántico. Valor: 11px `#8B93A5` con `margin-top:3px` (ranking) o `margin-bottom:4px` y el número inmediatamente debajo (transportadoras).
- Grids concretos:
  - Ranking de países → `1.4fr 1fr 1fr 1.2fr 0.8fr`: País · Contribución · Consolidado · % Entrega · Tendencia
  - Transportadoras → `1.3fr 1fr 1fr 0.8fr 0.8fr 1fr`: Transportadora · % Entrega · % Problema · p50 · p90 · Guías
  - Salud de conexiones → `0.6fr 1fr 1fr 1fr 1fr`: bandera · plataforma · tipo · estado (punto 7px + texto coloreado) · último sync (`#5B6272`)
  - Historial de cargas → `1fr 1.6fr 1.4fr 1fr`: fecha · archivo · resultado · usuario

### 4.5 Gráfico de cascada (P&L)
- Contenedor `height:240px; display:flex; align-items:flex-end; gap:26px; padding-left:6px`; cada columna `flex:1; flex-direction:column; align-items:center; gap:8px` con riel interno de `height:220px`.
- Escala: `scale = 220 / recaudo`.

| Barra | Color | Altura | Offset inferior | Radio |
|---|---|---|---|---|
| Recaudo | `#3A4152` | `220px` | 0 | `4px 4px 0 0` |
| −Producto | `#FF6259` | `producto × scale` | `(recaudo − producto) × scale` | `4px` (flotante) |
| −Flete | `#F5A83C` | `flete × scale` | `contribucion × scale` | `4px` (flotante) |
| Contribución | `#33E5B0` | `contribucion × scale` | 0 | `4px 4px 0 0` |

- Etiqueta bajo cada barra: 11px `#8B93A5` centrada, con `<br>` y el valor en `<b>` color `#E7EAF0`; la de Contribución va entera en `#33E5B0`.

### 4.6 Curva de maduración de cohortes
- `<svg width="100%" height="180" viewBox="0 0 360 180">`; línea base en `y=179`, color `rgba(255,255,255,0.08)`.
- Tres polilíneas, `fill:none`:
  - **Semana 1** — `#33E5B0`, `stroke-width:1.8` — `0,180 48,153 96,130 144,117 192,106 240,99 288,95 360,92`
  - **Semana 2** — `#F5A83C`, `stroke-width:1.8` — `0,180 48,158 96,138 144,126 192,116 240,110 288,106 360,101`
  - **Semana 3 (en curso)** — `#5B6272`, `stroke-width:1.6`, `stroke-dasharray:3,3`, truncada — `0,180 48,162 96,145 144,133 192,124`
- Leyenda debajo: `display:flex; gap:16px; font-size:11px; color:#8B93A5`, cada ítem con `●` del color de la serie.

### 4.7 Barras de aging
- Contenedor `display:flex; align-items:flex-end; gap:16px; height:150px`; columnas `flex:1; flex-direction:column; align-items:center; gap:8px`.
- Altura de barra: `round(140 × valor / max(valores))`; ancho 100%; radio `4px 4px 0 0`.
- Buckets y colores: `0–3d #33E5B0` · `4–7d #5FCB9E` · `8–12d #F5A83C` · `13+d #FF6259`.
- Etiqueta: 11px `#8B93A5` con el rango + `<br>` + conteo en `<b style="color:#E7EAF0">`.

### 4.8 Scatter con cuadrantes (Margen × % Entrega)
- `<svg width="100%" height="260" viewBox="0 0 380 260">`.
- Ejes: vertical `x=190, y 0→240`; horizontal `y=120, x 0→380`; ambos `stroke:rgba(255,255,255,0.08)`.
- Etiquetas `<text>` 10px `#5B6272`: `Arreglar logística` (10,14) · `Escalar` (284,14) · `Matar` (10,234) · `Subir precio` (284,234).
- Burbujas: `cx = (entrega/100) × 360`, `cy = 240 − (margin/100) × 240`, `r = max(8, round(6 + guias/40))`, `fill-opacity:0.75`, color por producto.
- Leyenda: `display:flex; flex-wrap:wrap; gap:10px 16px; margin-top:6px`; ítems 11px `#8B93A5` con punto de 7px del color del producto.

### 4.9 Semáforo geográfico (lista de provincias)
- Card `overflow:hidden` con header estándar; cada fila `padding:12px 20px; border-top:1px solid rgba(255,255,255,0.05); cursor:pointer`.
- Fila superior (`space-between`): nombre 13px/600 + porcentaje 13px/700 coloreado.
- Debajo, barra full-width `height:6px`, track `#1B212C`, radio 4px, fill `width:{entrega}%` del mismo color.
- **Umbrales exactos:** `≥75% → #33E5B0` · `≥60% → #F5A83C` · `<60% → #FF6259`. Se calculan en render, no vienen en los datos.

### 4.10 Card de alerta
**Copiloto:**
```
#12161F / border 1px rgba(<severidad>,0.25) / radius 10px / padding 14px
├ severidad  10.5px/700 en color de severidad, mb 6px   ("CRÍTICA · Logística")
├ titular    13px, line-height 1.5, mb 8px
├ impacto    12px #8B93A5 con <b> en color de severidad, mb 10px
└ acción     inline-block · padding 6px 12px · bg rgba(<severidad>,0.12)
             · color severidad · radius 7px · 12px/700
```
**Móvil:** `#12161F`, `border-left:3px solid <severidad>`, radio `8px`, `padding:12px 14px`, etiqueta 11px/700 + texto 13px/lh 1.4.

**Banner "Resumen IA":** `#12161F`, borde `rgba(51,229,176,0.20)`, radio 12px, padding 20px, `display:flex; gap:14px`; avatar 34×34 radio 9px `rgba(51,229,176,0.12)` con "IA" 15px/700 `#33E5B0`; etiqueta 13px/700/`.04em`/uppercase `#33E5B0`; cuerpo 14px/lh 1.65 `#DADEE6`.

### 4.11 Panel Copiloto IA (slide-over)
- `position:fixed; top:0; right:0; width:420px; height:100vh; z-index:50`; fondo `#0D1017`; `border-left:1px solid rgba(255,255,255,0.09)`; sombra `-20px 0 40px rgba(0,0,0,0.35)`. **No hay backdrop.**
- Header `padding:18px 20px`, `border-bottom:1px solid rgba(255,255,255,0.07)`; título 14.5px/700; cerrar "×" 18px `#5B6272`.
- Cuerpo `flex:1; overflow-y:auto; padding:20px; gap:20px`. Secciones: **Resumen del día** (label 11px/700/.05em `#33E5B0`) → **Alertas inteligentes** (label `#5B6272`) → **Pregúntale a tus datos**.
- Chips de sugerencia: 11.5px, `padding:6px 10px`, `#12161F`, borde `rgba(255,255,255,0.10)`, radio `16px`, color `#C4CAD6`.
- Input: caja `flex:1`, `#12161F`, borde `rgba(255,255,255,0.10)`, radio 9px, `padding:10px 12px`, placeholder 13px `#5B6272`; botón enviar 38×38, radio 9px, `#33E5B0`, flecha `#06110C`.
- **FAB**: `position:fixed; bottom:28px; right:28px; width:52px; height:52px; border-radius:16px; background:#33E5B0; z-index:40; box-shadow:0 8px 24px rgba(51,229,176,0.35)`; icono de chat 22px relleno `#06110C`.

### 4.12 Dropzone de carga
- **Onboarding**: `border:2px dashed rgba(51,229,176,0.35); border-radius:16px; padding:56px 20px; background:rgba(51,229,176,0.04)`; icono 34px `#33E5B0` (`margin:0 auto 14px`); título 15px/600; subtítulo 12.5px `#5B6272`.
- **Cargar datos**: `border:2px dashed rgba(51,229,176,0.30); border-radius:14px; padding:44px; background:rgba(51,229,176,0.03)`; icono 30px; título 14.5px/600; subtítulo 12px `#5B6272`.

### 4.13 Fila de archivo en procesamiento
- **Éxito / en progreso**: `#12161F`, borde card, radio 10px, `padding:14px 18px`. Línea 1 `space-between` 13px: nombre en 600 + detección en `#5B6272`. Barra `height:5px`, track `#1B212C`, radio 4px, fill `#33E5B0` (`100%` o `64%`), `margin-bottom:8px`. Línea 3 12px `#8B93A5` con conteos coloreados (`#33E5B0` nuevas, `#F5A83C` actualizadas).
- **Error**: fondo `rgba(255,98,89,0.06)`, borde `rgba(255,98,89,0.30)`, radio 10px; nombre y estado en `#FF6259`; detalle 12px `#F5A0A0` con `margin-top:6px`. Sin barra de progreso.

### 4.14 Chips / pills

| Variante | Estilo |
|---|---|
| API | 10px/700 · `padding:3px 7–8px` · radio 5px · `rgba(51,229,176,0.12)` / `#33E5B0` |
| Archivo | idem con `rgba(245,168,60,0.12)` / `#F5A83C` |
| Sesión | idem con `rgba(139,147,165,0.15)` / `#8B93A5` |
| Rol Dueño | 11px/700 · `padding:3px 8px` · radio 5px · `rgba(51,229,176,0.10)` / `#33E5B0` |
| Rol Analista | `rgba(255,255,255,0.06)` / `#C4CAD6` |
| Rol Solo lectura | `rgba(255,255,255,0.04)` / `#8B93A5` |
| PRINCIPAL | 9px/700 · `padding:2px 6px` · radio 5px · `#33E5B0` / `#06110C` · `position:absolute; top:10px; right:10px` |
| Sugerencia (copiloto) | 11.5px · `padding:6px 10px` · radio 16px · `#12161F` + borde 0.10 |
| Botón móvil activo | `padding:6px 14px` · radio 16px · `#33E5B0` / `#06110C` · 11.5px/700 |
| Botón móvil inactivo | idem · `#12161F` + borde `rgba(255,255,255,0.10)` · `#8B93A5` · 600 |

### 4.15 Estados de widget

| Estado | Etiqueta encima | Estilo del widget |
|---|---|---|
| **Disponible** | 11px/700/.05em `#33E5B0` | Card normal + sparkline |
| **Degradado** | 11px/700/.05em `#F5A83C` | Card con `border:1px solid rgba(245,168,60,0.30)` y `overflow:hidden`; **banner superior** `background:rgba(245,168,60,0.12); color:#F5A83C; font-size:11px; font-weight:600; padding:7px 14px`; contenido debajo con `padding:20px` |
| **Bloqueado** | 11px/700/.05em `#FF6259` | Card `position:relative; overflow:hidden; min-height:118px`; contenido real con `filter:blur(4px); opacity:.4; pointer-events:none`; overlay `position:absolute; inset:0`, `background:rgba(11,14,20,0.55–0.60)`, centrado en columna con `gap:9–10px` y `padding:16–20px`, candado SVG 20–22px `#8B93A5`, mensaje 12–12.5px `#C4CAD6`, CTA `padding:6px 14px` / `7px 16px` en `#33E5B0`/`#06110C`, radio 7px, 11.5–12px/700, texto "Conectar →" |

- **Bloqueo inline** (tab Servicio, sin blur): `display:flex; align-items:center; gap:8px; font-size:12px; color:#5B6272; padding:8px 10px; background:#0F131A; border-radius:7px` con emoji 🔒.
- **Aviso de limitación** (una sola transportadora): `background:rgba(245,168,60,0.08); border:1px solid rgba(245,168,60,0.30); border-radius:10px; padding:14px 18px; font-size:13px; color:#F5A83C`.

### 4.16 Barra de progreso del wizard
`max-width:640px; margin-bottom:36px`. Fila de labels 11px/600 `#5B6272` (`Paso N de 4` a la izquierda, etiqueta del paso a la derecha), `margin-bottom:8px`. Barra `height:4px; background:#181D27; border-radius:4px; overflow:hidden`; fill `#33E5B0`, `width = step × 25%`, `transition: width .25s`.

### 4.17 Barra de ciclo de caja (apilada)
`height:8px; border-radius:5px; overflow:hidden; display:flex; margin-top:10px` → `35% #8B93A5` (En tránsito) · `40% #F5A83C` (Maduración) · `25% #33E5B0` (Cobrado). Leyenda debajo con `space-between`, 10.5px `#5B6272`, `margin-top:6px`.

### 4.18 Embudo de novedades
`max-width:480px; gap:10px`. Fila 1: label 11.5px `#8B93A5` + barra `height:20px; width:100%; background:#5B6272; border-radius:5px`. Fila 2: dos columnas en `display:flex; gap:10px`, con `flex` proporcional al valor — Recuperadas (`#33E5B0`) y Devolución (`#FF6259`), labels en el color de su serie.

### 4.19 Barras de confirmación por canal
Por canal: fila `space-between` 12.5px (nombre + valor en `<b>`), `margin-bottom:5px`; barra `height:7px`, track `#1B212C`, radio 4px, fill `width:{pct}%` — WhatsApp `#33E5B0`, Llamada `#5B6272`, Bot IA `#F5A83C`.

### 4.20 Mockup móvil
`width:375px; height:790px; background:#0B0E14; border:10px solid #1A1E27; border-radius:44px; overflow:hidden; box-shadow:0 30px 60px rgba(0,0,0,0.5)`. Notch: `position:absolute; top:0; left:50%; transform:translateX(-50%); width:130px; height:22px; background:#1A1E27; border-radius:0 0 14px 14px; z-index:2`. Contenido `height:100%; overflow-y:auto; padding-top:34px`.

### 4.21 Botones

| Tipo | Estilo |
|---|---|
| Primario | `background:#33E5B0; color:#06110C; font-weight:700; border-radius:8px` — `padding:10px 26px` (wizard), `9px 16px` (calibración), full-width `padding:12px` radio 9px 13.5px (móvil) |
| Secundario / ghost | `background:transparent; border:1px solid rgba(255,255,255,0.12–0.14); color:#8B93A5` (o `#C4CAD6`), `font-weight:600`, radio 8px, `padding:10px 22px` |
| Link de acción | 12.5px/600 `#33E5B0`, sin fondo ("+ Agregar país", "Copiar") |

### 4.22 Esqueletos y estados vacíos
No existen componentes de skeleton ni empty-state en el prototipo. El principio declarado es **"Un widget nunca se oculta: siempre enseña qué le falta a la operación"** — los tres estados de §4.15 sustituyen al vacío. La carga en curso se representa con la barra de progreso al 64% en la fila de archivo.

---

## 5. Layouts de pantalla

**Shell global:** `display:flex; height:100vh; width:100%; overflow:hidden; position:relative` → **[Sidebar 224/64px]** + **[Columna principal `flex:1; display:flex; flex-direction:column; min-width:0`]**. La columna principal contiene el topbar de 56px (condicional) y un área `flex:1; overflow-y:auto; position:relative`.

### 5.1 Onboarding (4 pasos) — sin topbar
Contenedor centrado en columna, `padding:60px 24px`, fondo `radial-gradient(circle at 50% 0%, rgba(51,229,176,0.06), transparent 55%)`.
1. Barra de progreso (`max-width:640px`, `margin-bottom:36px`).
2. Contenido del paso.
3. Botonera `display:flex; gap:10px; margin-top:40px` → "Atrás" (ghost) + "Continuar" / "Ir al Dashboard" (primario).

| Paso | Etiqueta | Contenido |
|---|---|---|
| 1 | Países | `max-width:640px`, centrado. H1 26px + subtítulo 14px. Grid `repeat(3,1fr); gap:14px` de 6 tarjetas `padding:22px 12px; border-radius:12px`: 3 seleccionadas (`#12161F` + `1.5px solid #33E5B0`, la primera con badge PRINCIPAL) y 3 disponibles (`#0F131A` + `1.5px dashed rgba(255,255,255,0.12)`, texto `#4C5364`). Bandera 32px (`margin-bottom:8px`) + nombre 13.5px/600 |
| 2 | Plataformas | `max-width:640px`. Encabezado de país 12px/700/uppercase/`.06em` `#5B6272` (`margin-bottom:10px`), luego grid `repeat(2,1fr); gap:10px` de tarjetas de plataforma (nombre 13.5px/600 + chip de tipo, `space-between`), `margin-bottom:24px` entre bloques de país |
| 3 | Histórico | `max-width:560px`, centrado. H1 + subtítulo + dropzone (`padding:56px 20px`) |
| 4 | Calibración | `max-width:520px`. H1 (`margin-bottom:20px`) + card `#12161F`, borde `rgba(51,229,176,0.25)`, radio 14px, padding 26px, alineada a la izquierda: icono 30×30 radio 8px `rgba(51,229,176,0.12)` con "i" 700 `#33E5B0`, texto 14.5px/lh 1.55, y dos botones (`margin-top:16px; gap:8px`) |

### 5.2 Dashboard Global
`padding:24px; display:flex; flex-direction:column; gap:20px`, en este orden:
1. **Grid de 4 KPIs** — `grid-template-columns:repeat(4,1fr); gap:14px`.
2. **Banner "Resumen del día"** (card IA, ancho completo).
3. **Ranking de países** — tabla de 5 columnas, 3 filas.
4. **Salud de conexiones** — tabla de 5 columnas, 6 filas.

### 5.3 Dashboard de País
`padding:24px; gap:20px`:
1. **Header** (`display:flex; align-items:center; gap:12px`): bandera 28px + bloque con nombre 20px/800/`-.02em` y `{periodo} · moneda {código}` 12.5px `#5B6272`.
2. **Tabs**: `display:flex; gap:4px; border-bottom:1px solid rgba(255,255,255,0.08)`. Cada tab `padding:12px 4px; margin-right:22px; font-size:13.5px; cursor:pointer`. Activo → `font-weight:700; color:#E7EAF0; border-bottom:2px solid #33E5B0`. Inactivo → `font-weight:500; color:#5B6272; border-bottom:2px solid transparent`. Orden: **Finanzas · Logística · Efectividad · Servicio**.
3. Contenido del tab:

**Finanzas** — grid `1.5fr 1fr; gap:16px`
- Izquierda: card "Cascada P&L del periodo" (`padding:22px`, título 13px/700 `margin-bottom:20px`, gráfico de 240px).
- Derecha: columna `gap:16px` con 3 cards → **CPD — Contribución por Pedido Despachado** (label 12px + valor 28px/800 `#33E5B0` + sparkline de 40px) · **Ciclo de caja** (frase 18px/700 con los días en `#33E5B0` + barra apilada + leyenda) · **Capital en tránsito** (valor 22px/800 + nota 11.5px `#5B6272`).

**Logística** — columna `gap:16px`
1. Aviso de limitación (condicional, si `logisticsLimited`).
2. Tabla "Transportadoras — cohorte madura" (6 columnas).
3. Grid `1fr 1fr; gap:16px` → "Curva de maduración de cohortes" · "Aging de guías abiertas".
4. Card "Embudo de novedades" (contenido `max-width:480px`).

**Efectividad** — columna `gap:16px`
1. Grid `1.4fr 1fr; gap:16px` → scatter "Margen × % de entrega por producto" (con su leyenda) · card "Semáforo geográfico" (lista de provincias, `display:flex; flex-direction:column`).
2. Grid `1fr 1fr; gap:16px` → card **CPA entregado** (bloqueada con blur+candado si `pautaLocked`; normal si no, valor 26px/800) · card **ROAS neto** (valor 26px/800 `#33E5B0`).

**Servicio** — grid `1.2fr 1fr; gap:16px`
- Izquierda: card "Confirmación por canal" con 3 barras (`gap:12px`); si `servicioBotLocked`, la fila de Bot IA se sustituye por el aviso inline 🔒. Debajo, "Tiempo primera respuesta" 12px `#5B6272` con el valor en `<b>#C4CAD6`, `margin-top:16px`.
- Derecha: card "Lift de confirmación" con borde `rgba(51,229,176,0.22)`, etiqueta 12px/700/uppercase/`.04em` `#33E5B0` y frase 14.5px/lh 1.6 con las cifras en `<b>#33E5B0`.

### 5.4 Cargar datos
`padding:24px; max-width:960px; display:flex; flex-direction:column; gap:20px`:
dropzone → lista de filas de archivo (`gap:10px`: 2 correctas/en progreso + 1 con error) → tabla "Historial de cargas" (4 columnas) → card "Recibir por correo" (`padding:18px`, `space-between`) con el buzón en pill `#0F131A` (borde `rgba(255,255,255,0.10)`, radio 8px, `padding:8px 12px`) y link "Copiar" en `#33E5B0`.

### 5.5 Configuración
`padding:24px; max-width:920px; gap:18px` — 3 cards `padding:20px` con título 13px/700 (`margin-bottom:14px`):
1. **Países** — 3 filas `#0F131A`, radio 8px, `padding:10px 14px`, `space-between` + link "+ Agregar país" (`margin-top:12px`).
2. **Usuarios y roles** — 3 filas con badge de rol a la derecha.
3. **Preferencias** — 4 filas `space-between` 13px (label + valor `#8B93A5`, o `#33E5B0` si es un valor sugerido).

### 5.6 Estados de widget
`padding:32px 24px; max-width:1100px`. Título 20px/800 (`margin-bottom:6px`) + subtítulo 13.5px `#8B93A5` (`margin-bottom:28px`). Grid `repeat(3,1fr); gap:18px` → Disponible · Degradado · Bloqueado, cada uno con su etiqueta de estado encima.

### 5.7 Sistema de diseño
`padding:32px 24px; max-width:1000px; display:flex; flex-direction:column; gap:28px`. Secciones con encabezado 11px/700/`.05em`/uppercase `#5B6272`:
**Color** (5 swatches de `width:120px`, alto 60px, radio 8px, borde `rgba(255,255,255,0.10)`, caption 11px `#8B93A5` con `Nombre · #HEX`) → **Tipografía — Inter, tabular numerals** (4 muestras) → **Chips y semáforo** (3 chips + 3 puntos con umbrales) → **Card KPI** (ejemplo de 220px de ancho) → **Espaciado** (sólo la línea de texto con la escala).

### 5.8 Móvil (galería)
`display:flex; justify-content:center; padding:36px 20px; gap:20px`. Sobre el teléfono, 3 botones pill: **Home · Alertas · Detalle**.
- **Home**: header `🇪🇨 Ecuador · Hoy` 12px `#8B93A5` (`margin-bottom:14px`) → grid `1fr 1fr; gap:10px` de 4 mini-KPIs (card radio 10px, `padding:14px`, label 11px `#8B93A5`, valor 18px/800) → card resumen IA compacta (radio 10px, padding 14px, borde `rgba(51,229,176,0.20)`, etiqueta 10.5px/700/uppercase `#33E5B0`, cuerpo 13px/lh 1.5 `#DADEE6`).
- **Alertas**: título "Alertas" 15px/700 + 3 cards con `border-left:3px` (CRÍTICA roja, ATENCIÓN ámbar, OPORTUNIDAD verde), `gap:10px`.
- **Detalle**: etiqueta de severidad 11px/700 → titular 16px/700/lh 1.4 → cuerpo 13px `#C4CAD6` lh 1.6 → caja de impacto `#0F131A` radio 8px padding 12px (label 11px `#5B6272` + valor 20px/800 `#FF6259`) → CTA primario full-width.

---

## 6. Datos de muestra (para el seed)

### Formatos de moneda
- **USD (Ecuador)**: `$ 20.896` · `$ 6,03` — punto de miles, coma decimal, espacio tras `$`.
- **COP (Colombia)**: `$ 83.584.000` · `$ 24.120` — sin decimales.
- **GTQ (Guatemala)**: `Q 6.420,00` · `Q 5,72` — siempre 2 decimales.
- Porcentajes `47,3%`, `51,3%`; puntos porcentuales `2,1 pts`; deltas `▲ 8,4%` / `▼ 6,0%`; ROAS `3,2x`; días `9,3 días`.
- Periodo mostrado en los tres países: `1–15 ago 2026`.

### KPIs globales
| Métrica | Valor | Delta |
|---|---|---|
| Contribución total (consolidada) | `$ 20.273` | ▲ 8,4% |
| CPD promedio | `$ 5,87` | ▲ 5,2% |
| % de entrega ponderado | `51,3%` | ▲ 2,1 pts |
| Capital en tránsito | `$ 7.025` | ▼ 6,0% (en verde) |

### Ranking de países
| País | Contribución | Consolidado | % Entrega (color de barra) | Tendencia |
|---|---|---|---|---|
| 🇪🇨 Ecuador | `$ 9.949` | `$ 9.949` | 47,3% — barra 47% `#F5A83C` | ▲ 3,1% |
| 🇨🇴 Colombia | `$ 39.796.000` | `$ 9.949` | 58,4% — barra 58% `#33E5B0` | ▲ 5,7% |
| 🇬🇹 Guatemala | `Q 2.930,00` | `$ 375` | 41,0% — barra 41% `#FF6259` | ▼ 1,4% |

### Salud de conexiones
| Bandera | Plataforma | Tipo | Estado | Último sync |
|---|---|---|---|---|
| 🇪🇨 | Effi | API | Sincronizado (`#33E5B0`) | hace 2 h |
| 🇪🇨 | Dropi | API | Sincronizado | hace 1 h |
| 🇪🇨 | Mastershop | Archivo | Pendiente (`#F5A83C`) | hace 14 h |
| 🇨🇴 | Effi | API | Sincronizado | hace 40 min |
| 🇨🇴 | Meta Ads | API | Sincronizado | hace 20 min |
| 🇬🇹 | Meta Ads | — | Sin conectar (`#FF6259`) | — |

### Ecuador — USD
- Recaudo `20896` · Producto `6138` · Flete `4809` · **Contribución `9949`** → `$ 20.896` / `$ 6.138` / `$ 4.809` / `$ 9.949`
- CPD `$ 6,03` · Capital en tránsito `$ 3.450` · Ciclo `9,3 días` · CPA `$ 4,10` · ROAS `3,2x`
- Flags: `logisticsLimited:false`, `pautaLocked:false`, `servicioBotLocked:false`
- Transportadoras: **Servientrega** 45,1% entrega / 29,0% problema / 922 guías / p50 4d / p90 9d — **Gintracom** 51,2% / 32,4% / 410 / 5d / 11d — **Laarcourier** 52,7% / 25,3% / 91 / 3d / 7d
- Aging: `0–3d 412` · `4–7d 298` · `8–12d 156` · `13+d 89`
- Novedades: entran `478`, recuperadas `196`, devolución `282`
- Productos: **Drenaje Linfático** 704 guías / 44,9% / 52% margen / `#33E5B0` — **Clorofila Detox** 426 / 46,7% / 38% / `#5FCB9E` — **Zooone** 428 / 33,9% / 22% / `#FF6259` — **Beevena Apitoxina** 89 / 53,9% / 48% / `#F5A83C`
- Provincias: Manabí 52,6% · Guayas 41,8% · El Oro 35,1% · Tungurahua 50,0%
- Confirmación: WhatsApp 68% · Llamada 22% · Bot IA 10% · Tiempo primera respuesta `6 min`
- Lift de confirmación: `18 pts`, `$ 2.340`

### Colombia — COP
- Recaudo `83584000` · Producto `24552000` · Flete `19236000` · **Contribución `39796000`**
- CPD `$ 24.120` · Capital `$ 13.800.000` · Ciclo `7,8 días` · CPA `$ 18.500` · ROAS `4,1x`
- Flags: todos desbloqueados
- Transportadoras: **Coordinadora** 61,2% / 19,5% / 850 / 3d / 6d — **Interrápidísimo** 55,8% / 24,1% / 520 / 4d / 8d — **Envía** 58,0% / 21,0% / 300 / 3d / 7d
- Aging: `380 · 240 · 130 · 60` — Novedades: entran `390`, recuperadas `210`, devolución `180`
- Productos: **Crema Reafirmante** 610 / 59,1% / 44% / `#33E5B0` — **Té Detox** 390 / 55,3% / 36% / `#5FCB9E` — **Faja Térmica** 340 / 62,7% / 41% / `#F5A83C` — **Sérum Facial** 220 / 66,9% / 50% / `#33E5B0`
- Provincias: Antioquia 68,4% · Valle del Cauca 60,1% · Cundinamarca 57,9% · Atlántico 49,5%
- Confirmación: 74 / 18 / 8 · `4 min` · Lift `15 pts`, `$ 6.200.000`

### Guatemala — GTQ
- Recaudo `6420` · Producto `1980` · Flete `1510` · **Contribución `2930`** → `Q 6.420,00` / `Q 1.980,00` / `Q 1.510,00` / `Q 2.930,00`
- CPD `Q 5,72` · Capital `Q 980,00` · Ciclo `11,2 días` · **CPA `null`** · **ROAS `null`**
- Flags: `logisticsLimited:true`, `pautaLocked:true`, `servicioBotLocked:true`
- Transportadora única: **Cargo Expreso** 41,0% / 33,5% / 512 guías / p50 5d / p90 12d
- Aging: `120 · 90 · 55 · 40` — Novedades: entran `165`, recuperadas `58`, devolución `98`
- Productos: **Colágeno Hidrolizado** 290 / 39,5% / 30% / `#FF6259` — **Multivitamínico** 222 / 43,2% / 34% / `#F5A83C`
- Provincias: Guatemala 44,2% · Quetzaltenango 38,6%
- Confirmación: 52 / 30 / 0 · `14 min` · Lift `9 pts`, `Q 410,00`

### Copys de IA (literales)
- **Resumen global**: *"Ayer despachaste 118 guías entre tus 3 países. Gintracom cayó 9 pts en Guayas esta semana; si la caída se sostiene, te cuesta ≈ $310/semana. El Drenaje Linfático mantiene 45% de entrega con margen 52%: aguanta escalar pauta."*
- **Resumen del copiloto**: igual pero abre con *"Ayer despachaste 118 guías."* (sin "entre tus 3 países").
- **CRÍTICA · Logística**: *"Gintracom cae 9 pts en Guayas esta semana"* — impacto `−$310/semana` — acción "Ver guías afectadas".
- **ATENCIÓN · Producto**: *"Zooone quema margen: 33,9% de entrega en 428 guías"* — impacto `−$740 este mes` — acción "Pausar producto en El Oro".
- **OPORTUNIDAD · Escalado**: *"Drenaje Linfático sostiene 45% entrega con 52% margen"* — potencial `+$1.200/mes escalando pauta` — acción "Subir pauta 20%".
- Sugerencias: *"¿qué producto me está quemando plata?"* · *"compara Servientrega vs Gintracom en agosto"*. Placeholder: *"Escribe tu pregunta…"*.
- Detalle móvil: *"Si la caída se sostiene, te cuesta ≈ $310/semana en contribución perdida sobre las 410 guías gestionadas por esta transportadora."* CTA: "Ver guías afectadas".
- Alertas móviles (lista): CRÍTICA *"Gintracom cae 9 pts en Guayas esta semana"* · ATENCIÓN *"Zooone con 33,9% de entrega — margen bajo"* · OPORTUNIDAD *"Drenaje Linfático aguanta más pauta"*.
- KPIs móviles (Ecuador · Hoy): Contribución `$ 9.949` · CPD `$ 6,03` · % Entrega `47,3%` · Capital tránsito `$ 3.450`.

### Cargar datos
- `guias_ago_ecuador.xlsx` — "Detectado: Effi · Reporte de guías (Ecuador)" — 100% — *1.649 filas · 212 guías nuevas · 431 actualizadas · 1.006 sin cambios · 0 errores*
- `pauta_colombia_ago.csv` — "Detectado: Meta Ads · Reporte de pauta (Colombia)" — 64%
- `movimientos_gt.xlsx` — "Falló la validación" — *"Recaudo promedio fuera de rango — posible archivo en centavos. No se cargó nada."*
- Historial: `15/08 · guias_ago_ecuador.xlsx · 212 nuevas · 431 act. · Alexander` — `14/08 · dropi_reporte.csv · 88 nuevas · 120 act. · Alexander`
- Buzón de ingesta: `ingesta-a8f3@norte.app`
- Dropzone: "Arrastra tus archivos aquí" / "Soporta múltiples archivos a la vez · CSV, XLSX"

### Configuración
- Países: `🇪🇨 Ecuador · principal` — "3 conexiones activas"; `🇨🇴 Colombia` — "3 conexiones activas"; `🇬🇹 Guatemala` — "1 conexión activa · pauta sin conectar" (en `#F5A83C`)
- Usuarios: **Alexander** (Dueño) · **Diana** (Analista) · **Brian** (Solo lectura)
- Preferencias: Moneda principal `USD ($)` · Ventana de maduración Ecuador `11 días (sugerido)` en `#33E5B0` · Ventana de maduración Colombia `15 días` · Tema `Oscuro`

### Onboarding
- Paso 1: seleccionados Ecuador (PRINCIPAL) / Colombia / Guatemala; disponibles 🇨🇷 Costa Rica, 🇲🇽 México, 🇵🇪 Perú. Título *"¿En qué países operas?"*; subtítulo *"Selecciona todos los que apliquen. Marca uno como principal — define tu moneda de consolidación."*
- Paso 2: *"Conecta tus plataformas"* / *"Cada país muestra solo las plataformas disponibles ahí."* — 🇪🇨 Ecuador: Effi (API), Dropi (API), Mastershop (Archivo), Hoko (Sesión); 🇬🇹 Guatemala: Effi (API), Carga manual (Archivo).
- Paso 3: *"Carga tu histórico"* / *"Con 90 días calibramos tu ventana real de maduración por país."* — dropzone *"Arrastra tus reportes de los últimos 90 días"* / *"CSV o Excel de Effi, Dropi, Mastershop — o el consolidado que tengas"*.
- Paso 4: *"Calibración inicial"* — *"Medimos tus entregas: tu ventana de maduración real en **Ecuador es 11 días**. ¿La aplicamos?"* — botones "Aplicar 11 días" (primario) / "Usar 15 días (default)" (ghost).
- Etiquetas de paso: `['Países','Plataformas','Histórico','Calibración']`.

---

## 7. Interacción y estado

Estado raíz del prototipo:
```
screen:          'global' | 'country' | 'upload' | 'settings' | 'onboarding' | 'widgets' | 'mobile' | 'design'
selectedCountry: 'ecuador' | 'colombia' | 'guatemala'
countryTab:      'finanzas' | 'logistica' | 'efectividad' | 'servicio'
range:           'hoy' | '7d' | '30d' | 'cohorte'      // default '30d'
copilotOpen:     false
sidebarCollapsed:false
onboardingStep:  1..4
mobScreen:       'home' | 'alerts' | 'detail'
```

Títulos del topbar por pantalla: `Dashboard Global` · `{nombre del país}` · `Estados de widget` · `Cargar datos` · `Configuración` · `Onboarding` · `Vista móvil` · `Sistema de diseño`.

- **Sidebar colapsable**: 224 → 64px; se ocultan etiquetas, wordmark y títulos de grupo, quedando sólo iconos y banderas; el chevron rota 180°.
- **Navegación**: cada ítem cambia `screen`; los tres de país fijan además `selectedCountry`. "Copiloto IA" **no navega**: alterna el slide-over y se pinta activo mientras el panel esté abierto.
- **Tabs de país**: cambio local de `countryTab`, sin recarga; el tab activo se conserva al cambiar de país.
- **Selector de rango**: cambia `range`; en el prototipo es puramente visual (no recalcula datos), pero es el punto de anclaje para el fetch real.
- **Topbar condicional**: oculto en las pantallas de preview (onboarding, mobile, design).
- **Copiloto**: FAB fijo abajo-derecha (`z-index:40`) abre/cierra un panel de **420px** anclado a la derecha, altura completa (`z-index:50`), con "×" para cerrar. Sin backdrop ni bloqueo de scroll.
- **Wizard**: `next` avanza hasta 4 y en el paso 4 salta a `screen:'global'`; `prev` no baja de 1. Progreso `step × 25%`; el CTA cambia a "Ir al Dashboard" en el paso 4.
- **Bloqueos derivados de los datos**:
  - `pautaLocked` (Guatemala) → widget de CPA con blur + overlay de candado + CTA "Conectar →".
  - `servicioBotLocked` → la fila de Bot IA se sustituye por el aviso inline 🔒.
  - `logisticsLimited` → banner ámbar sobre la tabla de transportadoras.
  - Cada país expone también los inversos (`pautaLockedInverse`, `servicioBotInverse`) para renderizar la variante desbloqueada.
- **Color derivado en runtime**: el color de cada provincia se calcula al renderizar con los umbrales `≥75 / ≥60 / else`; no viene en los datos semilla.
- **Drill-down móvil**: sólo la alerta CRÍTICA de la lista tiene handler y lleva a la vista Detalle; las otras dos no son clicables.
- **Hover**: el único hover definido es el de enlaces (`a:hover → #5CEFC4`). Todo elemento interactivo lleva `cursor:pointer` pero sin feedback visual — conviene añadir hovers propios (p. ej. elevar el fondo de fila o de nav item) al implementar.
- **Filas clicables sin handler**: las provincias del semáforo llevan `cursor:pointer`, señalando un drill-down previsto.
