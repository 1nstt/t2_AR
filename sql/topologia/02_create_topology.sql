-- =============================================================================
-- 2.0 TOPOLOGÍA · Paso 2: pgr_createTopology
-- Tolerancia: 5 metros (la geometría está en EPSG:32719, unidades en metros).
--   * Es la misma tolerancia del snapping DBSCAN del pipeline en Python.
--   * Los errores de digitalización medidos son de ~2 m (Purranque: 2,09 m).
--   * La prueba de sensibilidad (1 a 50 m) muestra que el número de vértices
--     y componentes casi no cambia en torno a 5 m.
-- Crea red_vertices_pgr y rellena red.source / red.target.
-- =============================================================================

SELECT pgr_createTopology('red', 5.0, 'geom', 'id', 'source', 'target', clean := true);

-- Tramos más cortos que la tolerancia quedan con source = target (lazos sin
-- sentido físico). Se guardan aparte y se quitan del grafo.
CREATE TABLE IF NOT EXISTS red_descartada (
    id bigint, id_red int, sub_id int, codigo_tipo text, longitud_km double precision,
    source bigint, target bigint, geom geometry(LineString, 32719));
INSERT INTO red_descartada
SELECT id, id_red, sub_id, codigo_tipo, longitud_km, source, target, geom
FROM red WHERE source = target;
DELETE FROM red WHERE source = target;

-- Llena cnt (grado) y chk (posibles huecos) en red_vertices_pgr
SELECT pgr_analyzeGraph('red', 5.0, 'geom', 'id', 'source', 'target');
