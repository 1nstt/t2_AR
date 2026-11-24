-- =============================================================================
-- 2.0 TOPOLOGÍA · Paso 5: enlaces sintéticos (no vienen en los datos)
-- Todos quedan con sintetico = true, codigo_tipo = 'CONECTOR' y una
-- justificación. Dos reglas:
--
--  R1 · Hueco entre trazados (<= 500 m): un extremo libre (grado 1) de una
--       componente queda a <= 500 m de un tramo de OTRA componente. Se une el
--       extremo con el punto más cercano del tramo (que se corta ahí). Uno por
--       par de componentes (el más corto).
--  R2 · Continuidad troncal norte-sur: las dos componentes más largas (norte y
--       sur) quedan separadas por más de 500 m. Se unen con un solo conector
--       entre su par de vértices más cercano.
--
-- Al terminar se vuelve a correr 02_create_topology.sql y 04_conectividad.sql.
-- =============================================================================

DROP TABLE IF EXISTS enlaces_sinteticos;
CREATE TABLE enlaces_sinteticos (
    id            serial PRIMARY KEY,
    regla         text,
    comp_desde    text,
    comp_hasta    text,
    vertice_desde bigint,
    arista_hasta  bigint,
    vertice_hasta bigint,
    dist_m        double precision,
    justificacion text,
    geom          geometry(LineString, 32719)
);

-- R1
INSERT INTO enlaces_sinteticos (regla, comp_desde, comp_hasta, vertice_desde, arista_hasta, dist_m, justificacion, geom)
SELECT DISTINCT ON (LEAST(ca.nombre, cb.nombre), GREATEST(ca.nombre, cb.nombre))
       'R1', ca.nombre, cb.nombre, v.id, e.id, ST_Distance(v.the_geom, e.geom),
       format('Hueco entre trazados: extremo libre de %s a %s m de un tramo %s de %s '
              '(mayor que la tolerancia de 5 m, menor o igual a 500 m).',
              ca.nombre, round(ST_Distance(v.the_geom, e.geom)::numeric, 1), e.codigo_tipo, cb.nombre),
       ST_MakeLine(v.the_geom, ST_ClosestPoint(e.geom, v.the_geom))
FROM red_vertices_pgr v
JOIN resumen_componentes ca ON ca.componente = v.componente
CROSS JOIN LATERAL (
    SELECT r.* FROM red r
    WHERE r.componente <> v.componente AND ST_DWithin(v.the_geom, r.geom, 500)
    ORDER BY v.the_geom <-> r.geom LIMIT 1
) e
JOIN resumen_componentes cb ON cb.componente = e.componente
WHERE v.cnt = 1
ORDER BY LEAST(ca.nombre, cb.nombre), GREATEST(ca.nombre, cb.nombre), ST_Distance(v.the_geom, e.geom);

-- R2 (solo si las dos componentes mayores siguen a más de 500 m)
INSERT INTO enlaces_sinteticos (regla, comp_desde, comp_hasta, vertice_desde, vertice_hasta, dist_m, justificacion, geom)
SELECT 'R2', a.nombre, b.nombre, va.id, vb.id, ST_Distance(va.the_geom, vb.the_geom),
       format('Continuidad troncal: las componentes %s (%s km) y %s (%s km) están separadas por %s m; '
              'la red troncal norte-sur es continua en la realidad, pero los datos no traen el tramo '
              'que las une. Conector entre el par de vértices más cercano.',
              a.nombre, a.km, b.nombre, b.km, round(ST_Distance(va.the_geom, vb.the_geom)::numeric, 0)),
       ST_MakeLine(va.the_geom, vb.the_geom)
FROM resumen_componentes a
JOIN resumen_componentes b ON a.n = 1 AND b.n = 2
JOIN red_vertices_pgr va ON va.componente = a.componente
CROSS JOIN LATERAL (
    SELECT vv.* FROM red_vertices_pgr vv WHERE vv.componente = b.componente
    ORDER BY vv.the_geom <-> va.the_geom LIMIT 1
) vb
WHERE NOT EXISTS (
    SELECT 1 FROM enlaces_sinteticos s
    WHERE (s.comp_desde, s.comp_hasta) IN ((a.nombre, b.nombre), (b.nombre, a.nombre))
)
ORDER BY ST_Distance(va.the_geom, vb.the_geom)
LIMIT 1;

-- Cortar los tramos que reciben un conector R1 en el punto de llegada
-- (si el punto de llegada está a <= 5 m de un extremo del tramo no hace falta
--  cortar: la tolerancia de pgr_createTopology lo une a ese vértice)
WITH c AS (
    SELECT s.arista_hasta AS id, ST_LineLocatePoint(r.geom, ST_EndPoint(s.geom)) AS fr
    FROM enlaces_sinteticos s JOIN red r ON r.id = s.arista_hasta
    WHERE s.regla = 'R1'
      AND ST_Distance(ST_EndPoint(s.geom), ST_StartPoint(r.geom)) > 5
      AND ST_Distance(ST_EndPoint(s.geom), ST_EndPoint(r.geom)) > 5
), nuevas AS (
    INSERT INTO red (id, id_red, sub_id, tipo_tramo, codigo_tipo, capa_origen, id_origen, geom,
                     sintetico, justificacion, prob_falla)
    SELECT (SELECT MAX(id) FROM red) + ROW_NUMBER() OVER (ORDER BY r.id, p.parte),
           r.id_red, r.sub_id * 10 + p.parte, r.tipo_tramo, r.codigo_tipo, r.capa_origen, r.id_origen,
           CASE p.parte WHEN 1 THEN ST_LineSubstring(r.geom, 0, c.fr)
                        ELSE ST_LineSubstring(r.geom, c.fr, 1) END,
           false, NULL, r.prob_falla
    FROM c JOIN red r USING (id) CROSS JOIN (VALUES (1), (2)) p(parte)
    RETURNING 1
)
DELETE FROM red WHERE id IN (SELECT id FROM c);

-- Agregar los conectores como aristas
INSERT INTO red (id, id_red, sub_id, tipo_tramo, codigo_tipo, capa_origen, id_origen, geom,
                 sintetico, justificacion)
SELECT (SELECT MAX(id) FROM red) + s.id, NULL, 1,
       'Conector sintético (' || s.regla || ')', 'CONECTOR', 'sintetico', 'S' || s.id, s.geom,
       true, s.justificacion
FROM enlaces_sinteticos s;

-- Longitud de los tramos nuevos (partes cortadas y conectores)
UPDATE red SET longitud_km = ST_Length(ST_Transform(geom, 4326)::geography) / 1000.0
WHERE longitud_km IS NULL;
