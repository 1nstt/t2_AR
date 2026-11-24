"""
=============================================================================
4.1 / 4.2 · MODELO DE FALLA del sismo M 8.8 y probabilidad de cada enlace
-----------------------------------------------------------------------------
1. Ejecuta sql/terremoto/02_modelo_falla.sql sobre la BD:
     parámetros con fuente, calibración con registros del 27F, funciones
     f_dist_ruptura_km / f_pga_g / f_p_puerto y UPDATE de cada enlace de
     red (dist_epicentro_km, dist_ruptura_km, pga_g, pgv_cms, tasa_cortes_km,
     prob_falla_8_8).
2. Valida la aceleración del modelo contra los contornos de PGA del ShakeMap
   de USGS (tabla terremoto_pga, apartado 4.0).
3. Compara con la alternativa de usar solo la distancia hipocentral.
4. Exporta resultados a datos/salida/modelo_falla/.
Uso:  docker compose run --rm app python scripts/modelo_falla.py
=============================================================================
"""
import sys

import pandas as pd

from db import DIRECTORIO_BASE, conexion, consulta

SQL = DIRECTORIO_BASE / "sql" / "terremoto" / "02_modelo_falla.sql"
SALIDA = DIRECTORIO_BASE / "datos" / "salida" / "modelo_falla"


class _Tee:
    def __init__(self, *f): self.f = f
    def write(self, s): [x.write(s) for x in self.f]
    def flush(self): [x.flush() for x in self.f]


def mostrar(titulo, df, archivo=None):
    print(f"\n{titulo}\n" + "-" * len(titulo))
    print(df.to_string(index=False))
    if archivo:
        df.to_csv(SALIDA / archivo, index=False, encoding="utf-8-sig")


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    log = open(SALIDA / "ejecucion_modelo_falla.log", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log)
    try:
        con = conexion()
        print("=" * 78 + "\n4.1 / 4.2 · Modelo de falla del sismo M 8.8 (27F)\n" + "=" * 78)
        print(f"-> SQL: {SQL.relative_to(DIRECTORIO_BASE)}")
        con.execute(SQL.read_text(encoding="utf-8"))

        mostrar("Epicentro (USGS, tabla terremoto)", consulta(con, """
            SELECT id, magnitud, profundidad_km, round(ST_X(geom)::numeric, 3) lon,
                   round(ST_Y(geom)::numeric, 3) lat FROM terremoto"""))
        mostrar("Parámetros del modelo (tabla modelo_parametros)", consulta(con, """
            SELECT parametro, round(valor::numeric, 6) valor, unidad, fuente FROM modelo_parametros"""),
                "parametros.csv")
        mostrar("Fragilidad por tipo de enlace (tabla fragilidad_tipo)",
                consulta(con, "SELECT codigo_tipo, modelo, k, justificacion FROM fragilidad_tipo"),
                "fragilidad_tipo.csv")

        mostrar("Calibración: PGA medida en el 27F vs modelo", consulta(con, """
            SELECT estacion, pga_obs_g AS pga_medida_g,
                   round((ST_Distance(ST_Transform(geom, 32719),
                         (SELECT ST_Transform(geom, 32719) FROM terremoto)) / 1000)::numeric, 1) AS dist_epicentro_km,
                   round(f_dist_ruptura_km(geom)::numeric, 1) AS dist_ruptura_km,
                   round(f_pga_g(geom)::numeric, 3) AS pga_modelo_g,
                   round((f_pga_g(geom) / pga_obs_g)::numeric, 2) AS modelo_sobre_medida
            FROM registro_27f ORDER BY dist_ruptura_km"""), "calibracion_registros.csv")

        # Alternativa: solo distancia hipocentral desde el epicentro (sin plano de ruptura)
        alt = consulta(con, """
            WITH d AS (
              SELECT pga_obs_g,
                     sqrt((ST_Distance(ST_Transform(geom, 32719),
                           (SELECT ST_Transform(geom, 32719) FROM terremoto)) / 1000) ^ 2
                          + mp('profundidad_hipocentro') ^ 2) AS r_hipo,
                     f_dist_ruptura_km(geom) AS r_rup
              FROM registro_27f)
            SELECT 'Distancia hipocentral (punto)' AS distancia,
                   round(avg(ln(pga_obs_g) - f_ln_pga_youngs(r_hipo))::numeric, 3) AS termino_evento,
                   round(exp(avg(ln(pga_obs_g) - f_ln_pga_youngs(r_hipo)))::numeric, 2) AS factor,
                   round(stddev_samp(ln(pga_obs_g) - f_ln_pga_youngs(r_hipo))::numeric, 3) AS sigma_ln
            FROM d
            UNION ALL
            SELECT 'Distancia al plano de ruptura (usada)',
                   round(avg(ln(pga_obs_g) - f_ln_pga_youngs(r_rup))::numeric, 3),
                   round(exp(avg(ln(pga_obs_g) - f_ln_pga_youngs(r_rup)))::numeric, 2),
                   round(stddev_samp(ln(pga_obs_g) - f_ln_pga_youngs(r_rup))::numeric, 3)
            FROM d""")
        mostrar("Medida de distancia: comparación de la calibración", alt, "comparacion_distancia.csv")

        # Validación contra ShakeMap de USGS: puntos de cada contorno de PGA en tierra chilena
        val = consulta(con, """
            WITH pts AS (
              SELECT c.valor / 100.0 AS pga_shakemap_g, (ST_DumpPoints(c.geom)).geom AS g
              FROM terremoto_pga c)
            SELECT pga_shakemap_g, count(*) AS puntos,
                   round(exp(avg(ln(f_pga_g(g))))::numeric, 3) AS pga_modelo_media_geom_g,
                   round(exp(avg(ln(f_pga_g(g) / pga_shakemap_g)))::numeric, 2) AS razon_modelo_shakemap
            FROM pts
            WHERE ST_X(g) BETWEEN -74.5 AND -69.5 AND ST_Y(g) BETWEEN -40 AND -31
            GROUP BY pga_shakemap_g ORDER BY pga_shakemap_g DESC""")
        mostrar("Validación: PGA del modelo sobre los contornos de PGA del ShakeMap (USGS)", val,
                "validacion_shakemap.csv")

        mostrar("Resumen por tipo de enlace (prob_falla_8_8)", consulta(con, """
            SELECT codigo_tipo, count(*) enlaces, round(sum(longitud_km)::numeric, 1) km,
                   count(*) FILTER (WHERE pga_g >= 0.1) con_pga_mayor_0_1g,
                   round(avg(prob_falla_8_8)::numeric, 4) p_media, round(max(prob_falla_8_8)::numeric, 4) p_max,
                   round(sum(prob_falla_8_8)::numeric, 1) cortes_esperados
            FROM red GROUP BY codigo_tipo ORDER BY cortes_esperados DESC"""), "resumen_tipo.csv")
        mostrar("Total", consulta(con, """
            SELECT count(*) enlaces, round(sum(prob_falla_8_8)::numeric, 1) cortes_esperados,
                   count(*) FILTER (WHERE prob_falla_8_8 >= 0.5) con_p_mayor_0_5,
                   count(*) FILTER (WHERE prob_falla_8_8 < 0.001) con_p_menor_0_001,
                   round(min(prob_falla_8_8)::numeric, 6) p_min, round(max(prob_falla_8_8)::numeric, 4) p_max
            FROM red"""))
        mostrar("Por banda de latitud (2°)", consulta(con, """
            SELECT (floor(ST_Y(ST_Transform(ST_LineInterpolatePoint(geom, 0.5), 4326)) / 2) * 2)::int AS lat_desde,
                   count(*) enlaces, round(avg(pga_g)::numeric, 3) pga_media_g,
                   round(avg(prob_falla_8_8)::numeric, 4) p_media, round(sum(prob_falla_8_8)::numeric, 1) cortes_esperados
            FROM red GROUP BY 1 ORDER BY 1 DESC"""), "resumen_latitud.csv")

        cols = """id, id_red, codigo_tipo, round(longitud_km::numeric, 2) km,
                  round(ST_Y(ST_Transform(ST_LineInterpolatePoint(geom, 0.5), 4326))::numeric, 3) lat,
                  round(ST_X(ST_Transform(ST_LineInterpolatePoint(geom, 0.5), 4326))::numeric, 3) lon,
                  round(dist_epicentro_km::numeric, 1) dist_epi_km, round(dist_ruptura_km::numeric, 1) dist_rup_km,
                  round(pga_g::numeric, 3) pga_g, round(tasa_cortes_km::numeric, 4) cortes_km,
                  round(prob_falla_8_8::numeric, 4) prob_falla_8_8"""
        mostrar("Enlaces MÁS afectados", consulta(con, f"SELECT {cols} FROM red ORDER BY prob_falla_8_8 DESC LIMIT 15"),
                "enlaces_mas_afectados.csv")
        mostrar("Enlaces MENOS afectados", consulta(con, f"SELECT {cols} FROM red ORDER BY prob_falla_8_8 ASC LIMIT 10"),
                "enlaces_menos_afectados.csv")
        consulta(con, f"SELECT {cols}, source, target, sintetico FROM red ORDER BY id").to_csv(
            SALIDA / "enlaces_prob_falla_8_8.csv", index=False, encoding="utf-8-sig")
        print("\nred actualizada (prob_falla_8_8 y columnas del cálculo) | datos/salida/modelo_falla/")
    finally:
        sys.stdout = sys.__stdout__
        log.close()


if __name__ == "__main__":
    main()
