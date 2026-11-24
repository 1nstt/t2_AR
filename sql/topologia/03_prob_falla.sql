-- =============================================================================
-- 2.0 TOPOLOGÍA · Paso 3: probabilidad de falla por enlace (0 por defecto)
-- Se agrega a cada arista; las secciones siguientes la modificarán por escenario.
-- =============================================================================

ALTER TABLE red DROP COLUMN IF EXISTS prob_falla;
ALTER TABLE red
    ADD COLUMN prob_falla double precision NOT NULL DEFAULT 0
    CONSTRAINT prob_falla_rango CHECK (prob_falla >= 0 AND prob_falla <= 1);

COMMENT ON COLUMN red.prob_falla IS
    'Probabilidad de falla del enlace (0 a 1). Valor inicial 0 para todos los tramos.';
