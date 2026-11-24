-- =============================================================================
-- 3.0 ATTR · Cálculo EXACTO en la base de datos (caso determinista)
-- Vale cuando todos los enlaces tienen prob_falla 0 o 1: un enlace con
-- prob_falla = 1 está caído y se excluye; el resto está operativo.
-- Entonces R_ij vale 1 si i y j quedan en la misma componente y 0 si no, y
--     ATTR = sum_c C(k_c, 2) / C(n, 2)
-- donde k_c es el número de nodos clave en la componente c y n el total de
-- nodos clave.
-- {filtro_aristas} lo reemplaza scripts/attr.py (por ejemplo, "AND NOT sintetico").
-- =============================================================================
WITH cc AS (
    SELECT node, component
    FROM pgr_connectedComponents(
        'SELECT id, source, target, 1 AS cost, 1 AS reverse_cost
         FROM red WHERE prob_falla < 1 {filtro_aristas}')
), k AS (
    -- un vértice que quedó sin aristas forma su propia componente
    SELECT n.nombre, COALESCE(cc.component, -n.id_vertice) AS comp
    FROM nodo_clave_vertice n LEFT JOIN cc ON cc.node = n.id_vertice
), g AS (
    SELECT comp, COUNT(*) AS k_c FROM k GROUP BY comp
)
SELECT (SELECT COUNT(*) FROM k)                                    AS n_nodos_clave,
       (SELECT COUNT(*) FROM k) * ((SELECT COUNT(*) FROM k) - 1) / 2 AS pares,
       SUM(k_c * (k_c - 1) / 2)                                    AS pares_conectados,
       SUM(k_c * (k_c - 1) / 2)::numeric
         / ((SELECT COUNT(*) FROM k) * ((SELECT COUNT(*) FROM k) - 1) / 2) AS attr,
       COUNT(*)                                                    AS componentes_con_nodos_clave
FROM g;
