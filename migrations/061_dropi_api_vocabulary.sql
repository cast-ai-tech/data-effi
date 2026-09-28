-- =============================================================================
-- 061 - Dropi por API: el catálogo lo dice y el vocabulario de estados crece
-- =============================================================================
--
-- 1. EL CATÁLOGO. Dropi sigue siendo una plataforma de archivo (`auth_type`
--    'file'): la pantalla de carga filtra por ese valor y cambiarlo sacaría a
--    Dropi de "Cargar datos". La API es una SEGUNDA forma de recibir los mismos
--    datos, y eso se dice con `source_mode = 'api'` en la conexión (042), no con
--    el tipo de la plataforma. El `setup_hint` ahora nombra las dos vías.
--
-- 2. ESTADOS QUE LA API PUEDE DEVOLVER Y EL EXPORT NO TRAÍA. CANDIDATOS: salen
--    de la lista de estados que muestra el panel de Dropi, no de una respuesta
--    real de la API todavía. `resolve_status` reporta por nombre cualquier
--    grafía que no reconozca, así que el primer sync real dice cuáles faltan.
--    Solo entran los de significado inequívoco; "RECHAZADO" NO se mapea a
--    propósito: en Dropi puede ser el cliente que no recibió (devolución) o el
--    proveedor que no despachó (cancelada), y adivinar corrompería el % de
--    devolución. VERIFICAR CON CUENTA REAL.
--
--        PENDIENTE CONFIRMACION      -> created     (la orden existe, nada salió)
--        EN BODEGA TRANSPORTADORA    -> picked_up   (la transportadora la tiene)
--        EN PROCESO DE DEVOLUCION    -> returning   (vuelve al origen)
--
--    Espejo en pipeline/mapping.py::STATUS_ALIASES (el test de store_pg compara
--    los dos en ambas direcciones).
--
-- Depende de: 047. Idempotente.
-- =============================================================================

UPDATE core.platform SET
    setup_hint = 'Dos formas: exporta el reporte de órdenes de Dropi y súbelo, o pega el '
                 'token de integración de tu cuenta (Dropi → Integraciones) en '
                 'Gestionar y Master Data trae las órdenes solo, varias veces al día.'
WHERE code = 'dropi';

INSERT INTO core.status_alias (platform_code, alias_norm, status_code) VALUES
    ('dropi', 'pendiente confirmacion',    'created'),
    ('dropi', 'en bodega transportadora',  'picked_up'),
    ('dropi', 'en proceso de devolucion',  'returning')
ON CONFLICT (platform_code, alias_norm) DO NOTHING;
