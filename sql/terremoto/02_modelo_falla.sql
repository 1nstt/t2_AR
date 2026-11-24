-- =============================================================================
-- 4.1 / 4.2 · MODELO DE FALLA del sismo M 8.8 (27F) y probabilidad de cada enlace
-- -----------------------------------------------------------------------------
-- Parte del epicentro cargado desde USGS (tabla terremoto, apartado 4.0) y
-- calcula para cada enlace de la tabla red (apartado 2.0):
--   1. distancia al epicentro            ST_Distance en EPSG:32719 (metros)
--   2. distancia a la ruptura (Rrup)     plano de falla que nace en el hipocentro
--   3. PGA   Youngs et al. (1997) + término de evento calibrado con registros 27F
--   4. PGV   Newmark y Hall (1982): 91 cm/s por g
--   5. P(falla)
--        cable (FIBRA, CAMINO, POSTE, CONECTOR): ALA (2001)
--             RR = K * 0.002416 * PGV  [cortes/km];  P = 1 - exp(-RR * L)
--        FERRY: falla de cualquiera de sus dos terminales portuarios
--             P_t = Phi( ln(PGA/mediana) / beta );  P = 1 - (1-P_s)(1-P_t)
-- No modifica red.prob_falla (red en operación, 0 por defecto, apartado 2.0):
-- el resultado queda en red.prob_falla_8_8.
-- Se ejecuta con:  scripts/modelo_falla.py
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Parámetros (valor, unidad y fuente de cada uno)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS modelo_parametros CASCADE;
CREATE TABLE modelo_parametros (
    parametro text PRIMARY KEY,
    valor     double precision NOT NULL,
    unidad    text,
    fuente    text NOT NULL
);

INSERT INTO modelo_parametros
SELECT 'magnitud', magnitud, 'Mw', 'USGS, tabla terremoto (apartado 4.0)' FROM terremoto
UNION ALL
SELECT 'profundidad_hipocentro', profundidad_km, 'km', 'USGS, tabla terremoto (apartado 4.0)' FROM terremoto;

INSERT INTO modelo_parametros VALUES
 -- Geometría de la ruptura (falla finita)
 ('rumbo',            17.5, 'grados', 'Falla finita USGS (G. Hayes), citada en GEER (2010) §2.2'),
 ('manteo',           18.0, 'grados', 'Falla finita USGS (G. Hayes), citada en GEER (2010) §2.2'),
 ('largo_al_norte',  275.0, 'km',     'Zona de réplicas ~530 km (GEER 2010 §2.2), de ~33,6°S a ~38,3°S'),
 ('largo_al_sur',    255.0, 'km',     'Zona de réplicas ~530 km (GEER 2010 §2.2), de ~33,6°S a ~38,3°S'),
 ('ancho_sobre_hipo', 61.0, 'km',     'Ancho 180 km (GEER 2010 §2.2); borde superior a ~4 km de profundidad'),
 ('ancho_bajo_hipo', 119.0, 'km',     'Ancho 180 km (GEER 2010 §2.2); borde inferior a ~60 km de profundidad'),
 -- Youngs et al. (1997), sismo interplaca, roca, PGA (Tabla 2)
 ('youngs_c0',      0.2418, '-',      'Youngs et al. (1997), ec. de roca'),
 ('youngs_c1',      1.414,  '-',      'Youngs et al. (1997), ec. de roca'),
 ('youngs_c3',     -2.552,  '-',      'Youngs et al. (1997), Tabla 2 (PGA, roca)'),
 ('youngs_c4',      1.7818, '-',      'Youngs et al. (1997), ec. de roca'),
 ('youngs_c5',      0.554,  '-',      'Youngs et al. (1997), ec. de roca'),
 ('youngs_c6',      0.00607,'1/km',   'Youngs et al. (1997), ec. de roca (término de profundidad)'),
 ('youngs_zt',      0.0,    '-',      'Youngs et al. (1997): Zt = 0 para sismo interplaca'),
 -- PGA -> PGV
 ('pgv_por_g',     91.0,    'cm/s/g', 'Newmark y Hall (1982): v/a = 36 in/s por g en roca'),
 -- ALA (2001), tasa de reparaciones por propagación de ondas
 ('ala_coef',       0.002416,'cortes/km por cm/s', 'ALA (2001): RR = K1 x 0,00187 x PGV [rep/1000 pies, in/s], en km y cm/s'),
 -- Fragilidad de terminal portuario (ferry)
 ('puerto_mediana', 0.35,   'g',      'FEMA HAZUS-MH MR4, cap. 7 (puertos, estructuras de borde costero), daño moderado'),
 ('puerto_beta',    0.60,   '-',      'FEMA HAZUS-MH MR4, cap. 7 (puertos, estructuras de borde costero)');

-- Factor K de vulnerabilidad por tipo de enlace
DROP TABLE IF EXISTS fragilidad_tipo CASCADE;
CREATE TABLE fragilidad_tipo (
    codigo_tipo   text PRIMARY KEY,
    modelo        text NOT NULL CHECK (modelo IN ('ALA_2001', 'HAZUS_PUERTO')),
    k             double precision,
    justificacion text NOT NULL
);
INSERT INTO fragilidad_tipo VALUES
 ('FIBRA',    'ALA_2001', 0.3, 'Cable de fibra en ducto HDPE enterrado: muy dúctil y tolera deformación; se asimila al rango bajo de K1 de ALA (tuberías dúctiles 0,3-0,5).'),
 ('CONECTOR', 'ALA_2001', 0.3, 'Enlace sintético (apartado 2.0): se supone fibra enterrada como el resto de la troncal.'),
 ('CAMINO',   'ALA_2001', 0.5, 'Fibra a enterrar junto al camino: además del suelo, expuesta a derrumbes, puentes y terraplenes; K1 = 0,5 (dúctil, mitad del rango).'),
 ('POSTE',    'ALA_2001', 1.0, 'Cable aéreo en postación eléctrica: caída de postes, choque de conductores y dependencia de la red eléctrica; K1 = 1 (frágil).'),
 ('FERRY',    'HAZUS_PUERTO', NULL, 'Sin cable: el enlace se corta si queda fuera de servicio un terminal portuario (daño en muelles o suspensión por alerta de tsunami).');

-- Registros de aceleración del 27F para calibrar (GEER 2010, Tabla 2.1)
DROP TABLE IF EXISTS registro_27f CASCADE;
CREATE TABLE registro_27f (
    estacion text PRIMARY KEY,
    pga_obs_g double precision NOT NULL,
    fuente    text NOT NULL,
    geom      geometry(Point, 4326) NOT NULL
);
INSERT INTO registro_27f (estacion, pga_obs_g, fuente, geom)
SELECT e, pga, f, ST_SetSRID(ST_MakePoint(lon, lat), 4326) FROM (VALUES
 ('Santiago - Metro Mirador (SANM)',        0.24, -70.6060, -33.5135, 'GEER 2010 T2.1 (RENADIC)'),
 ('Santiago - CRS Maipú (CRS)',             0.56, -70.7719, -33.5087, 'GEER 2010 T2.1 (RENADIC)'),
 ('Santiago - Hosp. Tisné (FSR)',           0.30, -70.5792, -33.5006, 'GEER 2010 T2.1 (RENADIC)'),
 ('Santiago - Hosp. Sótero del Río (RIO)',  0.27, -70.5811, -33.5769, 'GEER 2010 T2.1 (RENADIC)'),
 ('Santiago - Cerro Calán (CLCH)',          0.23, -70.5369, -33.3961, 'GEER 2010 T2.1 (RENASIS)'),
 ('Santiago - Campus Antumapu (ANTU)',      0.27, -70.6335, -33.5691, 'GEER 2010 T2.1 (RENASIS)'),
 ('Cerro El Roble (ROC1)',                  0.19, -71.0156, -32.9759, 'GEER 2010 T2.1 (RENASIS)'),
 ('Viña del Mar - Marga Marga (VDMM)',      0.35, -71.5099, -33.0482, 'GEER 2010 T2.1 (RENADIC)'),
 ('Viña del Mar - Centro (VDMC)',           0.33, -71.5508, -33.0256, 'GEER 2010 T2.1 (RENADIC)'),
 ('Curicó - Hospital (CUR)',                0.47, -71.2364, -34.9808, 'GEER 2010 T2.1 (RENADIC)'),
 ('Concepción - Colegio San Pedro (CCSP)',  0.65, -73.0500, -36.8300, 'GEER 2010 T2.1 (RENASIS); sin coord. en la tabla: centro de Concepción'),
 ('Valdivia - Hospital (VLCH)',             0.14, -73.2390, -39.8306, 'GEER 2010 T2.1 (RENADIC)')
) AS t(e, pga, lon, lat, f);

-- -----------------------------------------------------------------------------
-- Funciones
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mp(p text) RETURNS double precision
LANGUAGE sql STABLE AS $$ SELECT valor FROM modelo_parametros WHERE parametro = p $$;

-- Distancia más corta (km) desde un punto en superficie al plano de ruptura.
-- El plano se ubica a partir del EPICENTRO de USGS: coordenadas locales en
-- metros (EPSG:32719) respecto del epicentro, giradas según el rumbo.
-- Fórmulas de Rrup para falla con manteo: Kaklamanos et al. (2011).
CREATE OR REPLACE FUNCTION f_dist_ruptura_km(p geometry) RETURNS double precision
LANGUAGE plpgsql STABLE AS $$
DECLARE
    epi   geometry := (SELECT ST_Transform(geom, 32719) FROM terremoto LIMIT 1);
    pt    geometry := ST_Transform(p, 32719);
    th    double precision := radians(mp('rumbo'));
    dl    double precision := radians(mp('manteo'));
    x     double precision := (ST_X(pt) - ST_X(epi)) / 1000.0;
    y     double precision := (ST_Y(pt) - ST_Y(epi)) / 1000.0;
    a     double precision := x * sin(th) + y * cos(th);        -- a lo largo del rumbo
    c     double precision := x * cos(th) - y * sin(th);        -- perpendicular, + hacia el manteo
    ztor  double precision := mp('profundidad_hipocentro') - mp('ancho_sobre_hipo') * sin(dl);
    w     double precision := mp('ancho_sobre_hipo') + mp('ancho_bajo_hipo');
    rx    double precision := c + mp('ancho_sobre_hipo') * cos(dl);   -- desde el borde superior
    ry0   double precision := greatest(0, a - mp('largo_al_norte'), -mp('largo_al_sur') - a);
    r     double precision;
BEGIN
    IF rx < ztor * tan(dl) THEN
        r := sqrt(rx ^ 2 + ztor ^ 2);
    ELSIF rx <= ztor * tan(dl) + w / cos(dl) THEN
        r := rx * sin(dl) + ztor * cos(dl);
    ELSE
        r := sqrt((rx - w * cos(dl)) ^ 2 + (ztor + w * sin(dl)) ^ 2);
    END IF;
    RETURN sqrt(r ^ 2 + ry0 ^ 2);
END $$;

-- ln PGA [g], Youngs et al. (1997), interplaca, roca (sin término de evento)
CREATE OR REPLACE FUNCTION f_ln_pga_youngs(rrup_km double precision) RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT mp('youngs_c0') + mp('youngs_c1') * mp('magnitud')
         + mp('youngs_c3') * ln(rrup_km + mp('youngs_c4') * exp(mp('youngs_c5') * mp('magnitud')))
         + mp('youngs_c6') * mp('profundidad_hipocentro') + 0.3846 * mp('youngs_zt')
$$;

-- Término de evento: promedio de ln(PGA medida / PGA Youngs) en los registros del 27F
INSERT INTO modelo_parametros
SELECT 'termino_evento', avg(ln(pga_obs_g) - f_ln_pga_youngs(f_dist_ruptura_km(geom))), '-',
       'Calibración con los 12 registros de la tabla registro_27f (GEER 2010, Tabla 2.1)'
FROM registro_27f;
INSERT INTO modelo_parametros
SELECT 'sigma_calibracion', stddev_samp(ln(pga_obs_g) - f_ln_pga_youngs(f_dist_ruptura_km(geom))), 'ln',
       'Desviación de los residuos de la calibración'
FROM registro_27f;

CREATE OR REPLACE FUNCTION f_pga_g(p geometry) RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT exp(f_ln_pga_youngs(f_dist_ruptura_km(p)) + mp('termino_evento'))
$$;

-- Probabilidad de que un terminal portuario quede fuera de servicio
CREATE OR REPLACE FUNCTION f_p_puerto(pga double precision) RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT 0.5 * (1 + erf(ln(pga / mp('puerto_mediana')) / (mp('puerto_beta') * sqrt(2.0))))
$$;

-- -----------------------------------------------------------------------------
-- 4.2 · Aplicación a cada enlace de red
-- -----------------------------------------------------------------------------
ALTER TABLE red
    DROP COLUMN IF EXISTS dist_epicentro_km,
    DROP COLUMN IF EXISTS dist_ruptura_km,
    DROP COLUMN IF EXISTS pga_g,
    DROP COLUMN IF EXISTS pgv_cms,
    DROP COLUMN IF EXISTS tasa_cortes_km,
    DROP COLUMN IF EXISTS prob_falla_8_8;
ALTER TABLE red
    ADD COLUMN dist_epicentro_km double precision,
    ADD COLUMN dist_ruptura_km   double precision,
    ADD COLUMN pga_g             double precision,
    ADD COLUMN pgv_cms           double precision,
    ADD COLUMN tasa_cortes_km    double precision,
    ADD COLUMN prob_falla_8_8    double precision
        CONSTRAINT prob_falla_8_8_rango CHECK (prob_falla_8_8 BETWEEN 0 AND 1);

-- Aceleración en cada vértice (la usan los ferries y el análisis de nodos)
DROP TABLE IF EXISTS vertice_sismo;
CREATE TABLE vertice_sismo AS
SELECT v.id,
       ST_Distance(v.the_geom, (SELECT ST_Transform(geom, 32719) FROM terremoto)) / 1000.0 AS dist_epicentro_km,
       f_dist_ruptura_km(v.the_geom) AS dist_ruptura_km,
       f_pga_g(v.the_geom)           AS pga_g
FROM red_vertices_pgr v;
ALTER TABLE vertice_sismo ADD PRIMARY KEY (id);

-- Distancias y aceleración: punto medio del enlace (geometría en metros, EPSG:32719)
UPDATE red r SET
    dist_epicentro_km = ST_Distance(r.geom, (SELECT ST_Transform(geom, 32719) FROM terremoto)) / 1000.0,
    dist_ruptura_km   = f_dist_ruptura_km(ST_LineInterpolatePoint(r.geom, 0.5)),
    pga_g             = f_pga_g(ST_LineInterpolatePoint(r.geom, 0.5));
UPDATE red SET pgv_cms = pga_g * mp('pgv_por_g');

-- Enlaces con cable: ALA (2001) + Poisson a lo largo del enlace
UPDATE red r SET
    tasa_cortes_km = f.k * mp('ala_coef') * r.pgv_cms,
    prob_falla_8_8 = 1 - exp(-f.k * mp('ala_coef') * r.pgv_cms * r.longitud_km)
FROM fragilidad_tipo f
WHERE f.codigo_tipo = r.codigo_tipo AND f.modelo = 'ALA_2001';

-- Ferry: falla de cualquiera de los dos terminales
UPDATE red r SET
    tasa_cortes_km = NULL,
    prob_falla_8_8 = 1 - (1 - f_p_puerto(vs.pga_g)) * (1 - f_p_puerto(vt.pga_g))
FROM vertice_sismo vs, vertice_sismo vt
WHERE r.codigo_tipo = 'FERRY' AND vs.id = r.source AND vt.id = r.target;

-- Control: ningún enlace sin probabilidad
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM red WHERE prob_falla_8_8 IS NULL) THEN
        RAISE EXCEPTION 'Hay enlaces sin prob_falla_8_8';
    END IF;
END $$;
