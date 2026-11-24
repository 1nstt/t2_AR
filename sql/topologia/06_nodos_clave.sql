-- =============================================================================
-- 2.0 TOPOLOGÍA · Paso 6: asociar los 55 nodos clave al vértice más cercano
-- Los nodos clave NO entran a pgr_createTopology: se asocian después para usarlos
-- como origen/destino en las secciones de ruteo (pgr_dijkstra).
-- =============================================================================

DROP TABLE IF EXISTS nodo_clave_vertice;
CREATE TABLE nodo_clave_vertice AS
SELECT k.nombre, k.tipo, k.rol,
       v.id                                   AS id_vertice,
       v.cnt                                  AS grado_vertice,
       rc.nombre                              AS componente,
       ROUND((ST_Distance(ST_Transform(k.geom, 4326)::geography,
                          ST_Transform(v.the_geom, 4326)::geography) / 1000.0)::numeric, 2) AS dist_km,
       k.geom
FROM nodos_clave k
CROSS JOIN LATERAL (
    SELECT * FROM red_vertices_pgr vv ORDER BY vv.the_geom <-> k.geom LIMIT 1
) v
JOIN resumen_componentes rc ON rc.componente = v.componente;
