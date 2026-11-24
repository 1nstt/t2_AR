#!/usr/bin/env bash
# =============================================================================
# 4.3 · Genera main.sql desde la BD y lo prueba en una base de datos VACÍA
# -----------------------------------------------------------------------------
# main.sql = infraestructura (topología del 2.0, nodos clave, sismo del 4.0)
#            + probabilidad de falla de cada enlace (4.2) + parámetros (4.1)
# Se ejecuta desde la raíz del proyecto, en WSL, con el contenedor db arriba:
#     bash scripts/generar_main_sql.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
SALIDA=datos/salida/main_sql
mkdir -p "$SALIDA"
PSQL="docker compose exec -T db psql -U ruteo -v ON_ERROR_STOP=1"

TABLAS=(
  red                 # enlaces: source, target, prob_falla (0), prob_falla_8_8 y su cálculo
  red_vertices_pgr    # vértices de pgr_createTopology
  enlaces_sinteticos  # los 4 conectores marcados y justificados
  nodos_clave         # 55 nodos clave (aterrizajes, IXP, DC, troncales)
  nodo_clave_vertice  # nodo clave -> vértice de la topología
  terremoto           # sismo M 8.8 desde USGS
  terremoto_pga       # contornos de PGA del ShakeMap
  terremoto_mmi       # contornos de intensidad del ShakeMap
  modelo_parametros   # parámetros del modelo de falla, con fuente
  fragilidad_tipo     # factor K por tipo de enlace
  registro_27f        # aceleraciones registradas usadas para calibrar
  vertice_sismo       # PGA en cada vértice
  attr_resumen        # ATTR en operación (3.1) y post 8.8 (5.0)
  resumen_componentes # componentes conexas de la red (2.0), las usa el mapa Leaflet
  usgs_descargas      # registro de los archivos descargados de USGS (4.0)
)
ARGS=()
for t in "${TABLAS[@]}"; do ARGS+=(-t "public.$t"); done

{
  cat <<'EOF'
-- =============================================================================
-- main.sql · Red troncal de Internet en Chile ante el sismo M 8.8 (27F)
-- Apartado 4.3: infraestructura + probabilidad de falla de cada enlace.
--
--   red.prob_falla      probabilidad en operación (0 por defecto, apartado 2.0)
--   red.prob_falla_8_8  probabilidad de falla tras el M 8.8 (apartados 4.1 y 4.2)
--
-- Carga en una base de datos vacía con PostGIS disponible en el servidor:
--     createdb red_chile
--     psql -d red_chile -f main.sql
-- (pgRouting no es necesario para cargar; sí para volver a rutear.)
-- Generado por scripts/generar_main_sql.sh a partir de la BD del proyecto.
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS postgis;
EOF
  docker compose exec -T db pg_dump -U ruteo -d ruteo --no-owner --no-privileges "${ARGS[@]}"
} > main.sql
echo "main.sql: $(wc -c < main.sql) bytes, $(grep -c '^CREATE TABLE' main.sql) tablas"

# ---- Prueba en una base de datos vacía ----
$PSQL -d ruteo -q -c "DROP DATABASE IF EXISTS prueba_main_sql" -c "CREATE DATABASE prueba_main_sql"
{
  echo "== Carga de main.sql en la base vacía prueba_main_sql =="
  $PSQL -d prueba_main_sql -q -c "\dt" | grep -c "table" | sed 's/^/tablas antes de cargar: /' || true
  $PSQL -d prueba_main_sql -q -f - < main.sql 2>&1 | grep -v "^SET\|set_config\|setval\|^-*$\|^ *[0-9]*$\|^ $" || true
  $PSQL -d prueba_main_sql -c "\dt public.*"
  $PSQL -d prueba_main_sql -c "
    SELECT (SELECT count(*) FROM red) AS enlaces,
           (SELECT count(*) FROM red WHERE sintetico) AS sinteticos,
           (SELECT count(*) FROM red_vertices_pgr) AS vertices,
           (SELECT count(*) FROM nodo_clave_vertice) AS nodos_clave,
           (SELECT count(*) FROM terremoto) AS sismos,
           (SELECT count(*) FROM red WHERE prob_falla_8_8 IS NULL) AS sin_probabilidad,
           round(sum(prob_falla_8_8)::numeric, 1) AS cortes_esperados,
           round(max(prob_falla_8_8)::numeric, 4) AS prob_max
    FROM red"
  $PSQL -d prueba_main_sql -c "
    SELECT id, codigo_tipo, round(longitud_km::numeric,2) km, round(pga_g::numeric,3) pga_g,
           round(prob_falla_8_8::numeric,4) prob_falla_8_8
    FROM red ORDER BY prob_falla_8_8 DESC LIMIT 5"
} 2>&1 | tee "$SALIDA/prueba_bd_vacia.txt"
$PSQL -d ruteo -q -c "DROP DATABASE prueba_main_sql"
echo "main.sql cargado sin errores en una base vacía (detalle en $SALIDA/prueba_bd_vacia.txt)"
