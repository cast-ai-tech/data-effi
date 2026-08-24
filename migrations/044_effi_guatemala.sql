-- =============================================================================
-- Data Effi - 044 - Effi operates in Guatemala.
--
-- Migration 001 seeded Effi's countries as Colombia, Ecuador and Panama. The
-- operator's own Effi export for Guatemala (1,230 guides, loaded 2026-08-24)
-- says otherwise, and the catalogue must describe reality, not the other way
-- round: without this row the per-country upload refuses "Effi" for GT with
-- "no opera en GT", which is false.
--
-- Depends on: 042. Idempotent.
-- =============================================================================

INSERT INTO core.platform_country (platform_code, country_code, notes)
VALUES ('effi', 'GT', 'Confirmado por el export real del operador, 2026-08-24.')
ON CONFLICT DO NOTHING;
