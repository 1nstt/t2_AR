-- =============================================================================
-- 2.0 TOPOLOGÍA · Paso 1: cortar los tramos en las uniones en T
-- Entrada : infra_original (4.497 tramos de infraestructura_limpia.geojson, EPSG:32719)
-- Salida  : uniones_t, cortes, red (tramos cortados, aún sin source/target)
--
-- pgr_createTopology solo crea vértices en los EXTREMOS de cada línea. Si un
-- tramo termina sobre el interior de otro (unión en T), sin cortar el segundo
-- no quedarían conectados. Se corta SOLO en uniones en T (extremo de un tramo a
-- <= 5 m del interior de otro) y NO en cruces en X, porque que dos trazados se
-- crucen en el plano (fibra bajo un camino, ferry sobre una ruta) no significa
-- que estén empalmados. pgr_nodeNetwork cortaría en todos los cruces.
-- =============================================================================

CREATE INDEX IF NOT EXISTS infra_original_geom_idx ON infra_original USING gist (geom);

-- 1.1 Puntos donde un extremo toca el interior de otro tramo (<= 5 m)
DROP TABLE IF EXISTS uniones_t;
CREATE TABLE uniones_t AS
WITH extremos AS (
    SELECT id_red, ST_StartPoint(geom) AS pt FROM infra_original
    UNION ALL
    SELECT id_red, ST_EndPoint(geom)   FROM infra_original
)
SELECT l.id_red                         AS id_tramo,
       e.id_red                         AS id_tramo_que_llega,
       ST_LineLocatePoint(l.geom, e.pt) AS fraccion,
       ST_Distance(l.geom, e.pt)        AS dist_m
FROM extremos e
JOIN infra_original l
  ON l.id_red <> e.id_red
 AND ST_DWithin(l.geom, e.pt, 5)
WHERE ST_Distance(ST_StartPoint(l.geom), e.pt) > 5   -- no es extremo con extremo
  AND ST_Distance(ST_EndPoint(l.geom),   e.pt) > 5;

-- 1.2 Puntos de corte por tramo: si varias uniones caen a menos de 5 m entre sí
--     sobre el mismo tramo se usa un solo corte (evita tramos de < 5 m que la
--     tolerancia convertiría en lazos source = target).
DROP TABLE IF EXISTS cortes;
CREATE TABLE cortes AS
WITH p AS (
    SELECT u.id_tramo, u.fraccion, u.fraccion * ST_Length(o.geom) AS pos_m
    FROM uniones_t u JOIN infra_original o ON o.id_red = u.id_tramo
), s AS (
    SELECT *, CASE WHEN pos_m - LAG(pos_m) OVER w <= 5 THEN 0 ELSE 1 END AS nuevo
    FROM p WINDOW w AS (PARTITION BY id_tramo ORDER BY pos_m)
), g AS (
    SELECT *, SUM(nuevo) OVER (PARTITION BY id_tramo ORDER BY pos_m) AS grupo FROM s
)
SELECT id_tramo, grupo, AVG(fraccion) AS fraccion, COUNT(*) AS uniones
FROM g GROUP BY id_tramo, grupo;

-- 1.3 Tabla de aristas: cada tramo original partido en sus puntos de corte
DROP TABLE IF EXISTS red CASCADE;
DROP TABLE IF EXISTS red_vertices_pgr;
DROP TABLE IF EXISTS red_descartada;
CREATE TABLE red AS
WITH f AS (
    SELECT id_red, 0.0::float8 AS fr FROM infra_original
    UNION SELECT id_red, 1.0 FROM infra_original
    UNION SELECT id_tramo, fraccion FROM cortes
), o AS (
    SELECT id_red, fr, LEAD(fr) OVER (PARTITION BY id_red ORDER BY fr) AS fr2 FROM f
)
SELECT (ROW_NUMBER() OVER (ORDER BY i.id_red, o.fr))::bigint          AS id,
       i.id_red,
       (ROW_NUMBER() OVER (PARTITION BY i.id_red ORDER BY o.fr))::int AS sub_id,
       i.tipo_tramo, i.codigo_tipo, i.capa_origen, i.id_origen,
       ST_LineSubstring(i.geom, o.fr, o.fr2)::geometry(LineString, 32719) AS geom
FROM o JOIN infra_original i USING (id_red)
WHERE o.fr2 IS NOT NULL;

ALTER TABLE red ADD PRIMARY KEY (id);
ALTER TABLE red ADD COLUMN longitud_km double precision;
UPDATE red SET longitud_km = ST_Length(ST_Transform(geom, 4326)::geography) / 1000.0;
ALTER TABLE red ADD COLUMN source bigint, ADD COLUMN target bigint;
ALTER TABLE red ADD COLUMN sintetico boolean NOT NULL DEFAULT false,
                ADD COLUMN justificacion text;
CREATE INDEX red_geom_idx ON red USING gist (geom);
