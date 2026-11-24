-- =============================================================================
-- TERREMOTO · Tablas del sismo M 8.8 del Maule (USGS)
-- Se recrean en cada ejecución de scripts/terremoto.py.
-- =============================================================================

DROP TABLE IF EXISTS terremoto CASCADE;
CREATE TABLE terremoto (
    id               text PRIMARY KEY,          -- ID del evento en USGS
    titulo           text,
    lugar            text,
    magnitud         numeric(3,1),
    tipo_magnitud    text,                      -- mww = magnitud de momento (fase W)
    fecha_utc        timestamptz,
    profundidad_km   numeric(6,2),              -- 3.ª coordenada del GeoJSON de USGS
    estado           text,                      -- reviewed / automatic
    tsunami          int,
    significancia    int,
    mmi_max          numeric(5,3),              -- intensidad máxima de ShakeMap
    cdi_max          numeric(4,1),              -- intensidad máxima de "Did You Feel It?"
    reportes_sentido int,
    alerta           text,                      -- PAGER (NULL si no hay)
    red              text,
    ids_asociados    text,
    url_evento       text,
    url_geojson      text,
    actualizado_utc  timestamptz,
    geojson          jsonb,                     -- respuesta completa de USGS, sin cambios
    geom             geometry(Point, 4326)      -- epicentro
);
CREATE INDEX terremoto_geom_idx ON terremoto USING gist (geom);

-- Contornos (isolíneas) de ShakeMap: intensidad MMI y aceleración máxima PGA
DROP TABLE IF EXISTS terremoto_mmi;
CREATE TABLE terremoto_mmi (
    id        serial PRIMARY KEY,
    evento_id text REFERENCES terremoto (id),
    valor     numeric,
    unidad    text,
    color     text,
    geom      geometry(MultiLineString, 4326)
);
CREATE INDEX terremoto_mmi_geom_idx ON terremoto_mmi USING gist (geom);

DROP TABLE IF EXISTS terremoto_pga;
CREATE TABLE terremoto_pga (LIKE terremoto_mmi INCLUDING ALL);
ALTER TABLE terremoto_pga ADD FOREIGN KEY (evento_id) REFERENCES terremoto (id);

-- Trazabilidad de cada archivo descargado
DROP TABLE IF EXISTS usgs_descargas;
CREATE TABLE usgs_descargas (
    id             serial PRIMARY KEY,
    archivo        text,
    url            text,
    descargado_utc timestamptz,
    bytes          bigint,
    sha256         text
);
