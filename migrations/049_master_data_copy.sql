-- Master Data - 049 - Rebranding: the copy that 012 seeded into core.platform.
--
-- The platform catalogue carries a one-line `setup_hint` per integration that
-- the Conexiones screen shows verbatim. Five of them named the product by its
-- old name, "Data Effi", and two used words a first-time operator does not
-- know ("POST", "URL y token"). 012 cannot be edited - scripts/migrate.py
-- verifies the checksum of every applied migration - so the rows are updated
-- here, in plain language, under the new name.

UPDATE core.platform
SET setup_hint = 'Sube tus reportes en Excel o CSV. Master Data reconoce de qué país es el archivo.'
WHERE code = 'manual_xlsx';

UPDATE core.platform
SET setup_hint = 'Master Data te da una dirección web y una clave. Cualquier herramienta que sepa enviar datos por internet (n8n, Make, Zapier o un programa propio) puede mandarte la información sola.'
WHERE code = 'webhook_generic';

UPDATE core.platform
SET setup_hint = 'Le das permiso a una carpeta de tu Google Drive y Master Data lee los reportes nuevos que aparezcan ahí.'
WHERE code = 'google_drive';

UPDATE core.platform
SET setup_hint = 'Escribe los números en una hoja de Google tuya para que tu equipo los vea sin entrar a Master Data.'
WHERE code = 'sheets_export';

UPDATE core.platform
SET setup_hint = 'Master Data envía cada alerta a la dirección que le des: n8n, Slack, WhatsApp, lo que uses.'
WHERE code = 'webhook_out';

-- "Tier 3" is not a phrase the operator uses; the screen calls it "con tu
-- usuario y contraseña".
UPDATE core.platform
SET setup_hint = 'Exporta los reportes de guías y movimientos y súbelos, o autoriza a Master Data a entrar con tu usuario y contraseña (riesgo alto).'
WHERE code = 'effi';

COMMENT ON VIEW mart.v_platform_catalogue IS
  'Every integration Master Data knows about, including the ones that do not work yet. Hiding those would make the roadmap invisible.';
