-- =============================================================================
-- 2.0 TOPOLOGÍA · Paso 4: conectividad
-- Componentes conexas con pgr_connectedComponents (grafo no dirigido) y
-- resumen por componente. Se vuelve a ejecutar después de agregar enlaces
-- sintéticos (si los hay).
-- =============================================================================

DROP TABLE IF EXISTS vertice_componente;
CREATE TABLE vertice_componente AS
SELECT node AS id_vertice, component AS componente
FROM pgr_connectedComponents(
    'SELECT id, source, target, longitud_km AS cost, longitud_km AS reverse_cost FROM red'
);
CREATE INDEX ON vertice_componente (id_vertice);

ALTER TABLE red_vertices_pgr DROP COLUMN IF EXISTS componente;
ALTER TABLE red_vertices_pgr ADD COLUMN componente bigint;
UPDATE red_vertices_pgr v SET componente = c.componente
FROM vertice_componente c WHERE c.id_vertice = v.id;

ALTER TABLE red DROP COLUMN IF EXISTS componente;
ALTER TABLE red ADD COLUMN componente bigint;
UPDATE red r SET componente = v.componente
FROM red_vertices_pgr v WHERE v.id = r.source;

-- Resumen por componente, numerado de mayor a menor longitud (C1, C2, ...)
DROP TABLE IF EXISTS resumen_componentes;
CREATE TABLE resumen_componentes AS
WITH a AS (
    SELECT componente,
           COUNT(*)                        AS aristas,
           ROUND(SUM(longitud_km)::numeric, 1) AS km,
           ROUND(SUM(longitud_km) FILTER (WHERE codigo_tipo = 'FIBRA')::numeric, 1) AS km_fibra,
           STRING_AGG(DISTINCT codigo_tipo, ', ' ORDER BY codigo_tipo) AS tipos,
           ROUND(ST_YMax(ST_Transform(ST_SetSRID(ST_Extent(geom)::geometry, 32719), 4326))::numeric, 2) AS lat_norte,
           ROUND(ST_YMin(ST_Transform(ST_SetSRID(ST_Extent(geom)::geometry, 32719), 4326))::numeric, 2) AS lat_sur
    FROM red GROUP BY componente
), v AS (
    SELECT componente, COUNT(*) AS vertices FROM red_vertices_pgr GROUP BY componente
)
SELECT ROW_NUMBER() OVER (ORDER BY a.km DESC, a.componente) AS n,
       'C' || ROW_NUMBER() OVER (ORDER BY a.km DESC, a.componente) AS nombre,
       a.componente, v.vertices, a.aristas, a.km, a.km_fibra, a.tipos, a.lat_norte, a.lat_sur
FROM a JOIN v USING (componente)
ORDER BY n;
