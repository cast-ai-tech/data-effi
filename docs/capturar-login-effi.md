# Capturar el login de Effi

Effi no publica API. Para que Master Data entre con la cuenta de cada comerciante
hacen falta seis datos que solo se ven mirando el tráfico del navegador: la ruta
del formulario, los nombres de los campos de usuario y contraseña, si hay token
CSRF, si la sesión vuelve como cookie o como JSON, y el nombre de esa cookie.

**La herramienta que los captura está en [`tools/effi-capture/`](../tools/effi-capture/README.md).**

## En corto

```bash
python -m scripts.empaquetar_captura     # genera captura-effi.zip
```

Envías ese `.zip` a quien tenga una cuenta de Effi. Dentro va una extensión de
Chrome con dos botones y unas instrucciones de diez minutos. Al terminar, esa
persona te pega en el chat un bloque de texto con el contrato — **sin
contraseñas, sin cookies, sin un solo valor**, porque la herramienta no los lee.

Si no puede instalar la extensión, el mismo paquete trae `analizador.html`, que
lee un `.har` sin instalar nada.

Para analizar un `.har` desde la terminal:

```bash
python -m scripts.extract_effi_contract "ruta/al/effi.har"
```

## Qué hacer con la respuesta

Está detallado en [`tools/effi-capture/README.md`](../tools/effi-capture/README.md),
pero en resumen: las líneas `EFFI_*` van al `.env`, las rutas de exportación se
comparan con `PERMISSION_PROBES` en `connectors/effi/permissions.py`, y entonces
—y solo entonces— se pone `LOGIN_CONTRACT_VERIFIED = True` en
`connectors/effi/auth.py`.

Hasta ese momento el login automático se niega a ejecutarse a propósito: mandar
una suposición sobre un formulario de login a producción arriesga bloquear la
cuenta de Effi de un comerciante real. Ver [tier3-politica.md](tier3-politica.md).
