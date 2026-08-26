# Cómo capturar tu conexión de Effi (10 minutos)

Gracias por ayudarnos con esto. Son clics en el navegador: no hay que saber
programar y no se instala nada en tu computador más allá de una extensión que
puedes quitar al terminar.

---

## Antes de nada: dos cosas que debes saber

**1. Esto no lee tu contraseña.** La herramienta anota los *nombres* de los
campos del formulario y las *direcciones* que Effi usa por dentro. No guarda lo
que escribes. Al final vas a ver exactamente lo que se envía, y lo puedes leer
entero antes de mandarlo.

**2. Aun así, usa una cuenta que puedas quemar.** Si puedes, crea en Effi un
usuario nuevo solo para esta prueba. Si tienes que usar el tuyo, no pasa nada,
pero es buena costumbre.

---

## Paso 1. Instala la extensión (una sola vez)

1. Descomprime la carpeta que te enviamos. Guárdala en un sitio donde no la
   borres por accidente — el Escritorio está bien.
2. Abre Chrome y escribe en la barra de direcciones: **`chrome://extensions`**
3. Arriba a la derecha, activa el interruptor **Modo de desarrollador**.
4. Aparecen unos botones nuevos. Pulsa **Cargar descomprimida**.
5. Busca la carpeta que descomprimiste y **entra en la subcarpeta `extension`**.
   Selecciónala y acepta.

Listo. Verás una tarjeta que dice *«Captura de conexión Effi»*.

> **¿Por qué en modo desarrollador?** Porque es una herramienta interna, no está
> publicada en la tienda de Chrome. Es el modo normal de instalar algo hecho a
> medida. Puedes desinstalarla en cualquier momento desde esa misma pantalla.

**Fíjate en lo que pide al instalarse:** solo acceso a `effi.com.co` y
`efficommerce.com`. No al resto de internet. Eso lo puedes comprobar tú en la
tarjeta de la extensión, y es a propósito.

## Paso 2. Fija la extensión a la barra (recomendado)

Al lado de la barra de direcciones hay un ícono de **pieza de rompecabezas** 🧩.
Haz clic, busca *«Captura de conexión Effi»* y pulsa el **alfiler** para que
quede siempre visible. Así no la pierdes de vista mientras trabajas.

## Paso 3. Cierra sesión en Effi

Importante. Si ya estás dentro, **sal**. Lo que necesitamos capturar es
justamente el momento de entrar.

## Paso 4. Graba

1. Haz clic en el ícono de la extensión.
2. Pulsa **Empezar a grabar**. El puntito se pone rojo.
3. **Cierra la ventanita** (haz clic fuera). La grabación sigue: no se para
   porque cierres el popup.
4. **Entra a Effi** con tu usuario y contraseña.
5. Ve a **Reporte de guías** → **Exportar**. Espera a que baje el archivo.
6. Ve a **Novedades de guías** → **Exportar**.
7. Ve a **Trazabilidad de dinero** (o Movimientos) → **Exportar**.
8. Si te sobra ánimo: **Artículos**, **Clientes** y **Gestión de novedades**.
   Cada uno que hagas es una parte más del tablero que va a funcionar.

**Consejo:** al exportar, pon un rango de fechas **corto**, de un día o dos. Nos
interesa la dirección, no los datos.

## Paso 5. Termina y envía

1. Vuelve a hacer clic en el ícono de la extensión.
2. Pulsa **Terminar y ver resultado**.
3. Arriba sale una frase que te dice **si salió bien o hay que repetir**. Léela.
4. Pulsa **Enviar automáticamente**.

Eso es todo. Llega solo, y en un segundo te sale la respuesta del servidor
diciéndote si quedó completa o si te faltó algo — mientras todavía tienes Effi
abierto y puedes repetir en el momento.

> **¿No ves ese botón?** Entonces tu paquete no trae el envío configurado, que
> también está bien: pulsa **Copiar para enviar** y pega el texto en el chat. Es
> el mismo resultado, con un paso más.
>
> **¿Dice que no se pudo enviar?** No perdiste nada. El texto sigue ahí abajo:
> púlsale a **Copiar** y mándalo por chat.

**Lo que se envía lo puedes leer antes**, en el recuadro de arriba del botón. Son
nombres de campos y direcciones: ni una contraseña, ni una cookie, ni un valor.

---

## Puedes repetirlo las veces que quieras

El botón **Grabar otra vez** borra lo anterior y empieza de cero. Si la primera
vez no salió, cierra sesión en Effi y repite: no se estropea nada y no hay
límite.

De hecho, si te sale bien a la primera, **hazlo una segunda vez** exportando los
reportes que te faltaron. Dos capturas nos sirven más que una.

---

## Si algo sale distinto

**«No se capturó el momento de entrar».**
La grabación empezó tarde, o Effi te reconoció y entró solo sin pedirte nada.
Cierra sesión en Effi, comprueba que de verdad estás fuera, y graba otra vez.

**«No se vio ninguna descarga».**
Los botones de Exportar no llegaron a dispararse. Repite esperando a que cada
archivo termine de bajar antes de pasar al siguiente.

**El contador se queda en 0 peticiones.**
La extensión no está viendo el tráfico. Casi siempre es que Effi te atiende en
una dirección distinta a las dos que vigila. Mira la barra de direcciones cuando
estés dentro de Effi: si **no** dice `effi.com.co` ni `efficommerce.com`,
mándanos esa dirección y te pasamos la extensión ajustada en un minuto.

**No puedo instalar extensiones (el trabajo no me deja).**
Hay un plan B sin instalar nada. Mira el final de este documento.

---

## Plan B: sin instalar la extensión

Sirve si usas Firefox, si no te dejan instalar extensiones, o si prefieres no
hacerlo.

1. Abre Effi **sin haber entrado**.
2. Presiona **F12**. Se abre un panel: es la consola del navegador, no rompe
   nada.
3. Pestaña **Network** (o **Red**).
4. Marca la casilla **Preserve log** (o **Conservar registro**). Sin esto la
   grabación se borra sola en cuanto Effi cambie de página.
5. Haz todo el Paso 4 de arriba: entrar y exportar los reportes.
6. En ese panel, clic derecho → **Save all as HAR with content** (o **Guardar
   todo como HAR**). Guárdalo en el Escritorio.
7. Abre el archivo **`analizador.html`** de la carpeta que te enviamos (doble
   clic; se abre en tu navegador).
8. **Arrastra el `.har`** encima. Sale el mismo resultado. Pulsa **Copiar** y
   pega el texto en el chat. (El plan B no envía solo: por eso el camino de la
   extensión es el recomendado.)

> **El `.har` sí contiene tu contraseña**, en texto legible. El `analizador.html`
> lo lee **en tu computador** y no lo sube a ningún lado — puedes desconectar el
> wifi y comprobarlo. Pero **no mandes el `.har` por chat ni por correo**, y
> **bórralo** cuando termines.

---

## Cuando acabemos

- Puedes **desinstalar la extensión** desde `chrome://extensions`.
- Si usaste tu cuenta real, **cámbiale la contraseña** en Effi.
- Si hiciste el plan B, **borra el `.har`**.

Gracias de verdad. Esto nos ahorra semanas de adivinar.
