"""Visor dinámico de la red troncal de Internet en Chile ante el M 8.8 (27F).

API FastAPI que lee en vivo la base PostGIS del proyecto (contenedor `db`) y
completa la metadata con las salidas de los scripts (`datos/salida/`).
El frontend (web/static/) consume estos endpoints.

    docker compose up -d web      ->  http://localhost:8000
"""
import csv
import json
import os
from functools import lru_cache
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

BASE = Path(__file__).resolve().parent.parent
SALIDA = BASE / "datos" / "salida"
ESTATICO = Path(__file__).resolve().parent / "static"

app = FastAPI(title="T2 Ruteo · Visor de la red troncal", docs_url="/api/docs")
app.add_middleware(GZipMiddleware, minimum_size=2000)


# -----------------------------------------------------------------------------
# Utilidades
# -----------------------------------------------------------------------------
def _dsn():
    return (f"host={os.environ.get('PGHOST', 'localhost')} port={os.environ.get('PGPORT', '5432')} "
            f"dbname={os.environ['PGDATABASE']} user={os.environ['PGUSER']} "
            f"password={os.environ['PGPASSWORD']}")


def filas(sql, params=None):
    with psycopg.connect(_dsn(), row_factory=dict_row) as con:
        return con.execute(sql, params).fetchall()


def valor(sql, params=None):
    with psycopg.connect(_dsn()) as con:
        return con.execute(sql, params).fetchone()[0]


def leer_csv(ruta):
    """Lee un CSV de datos/salida (los scripts lo escriben con BOM UTF-8)."""
    ruta = SALIDA / ruta
    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def json_crudo(texto):
    """El GeoJSON ya viene armado desde PostGIS: se devuelve tal cual."""
    return JSONResponse(content=json.loads(texto))


# -----------------------------------------------------------------------------
# Contexto: qué significa cada tipo de nodo clave y de enlace, con su fuente
# (resumen de apartados 1.0 y 4.1)
# -----------------------------------------------------------------------------
CRITERIO_NODO = {
    "Aterrizaje cable submarino": {
        "GENERA": "Aterrizaje de cable submarino internacional: punto donde entran a Chile los cables que traen el "
                  "contenido alojado en el extranjero.",
        "INTERCAMBIA": "Aterrizaje de cable doméstico (Prat, Fibra Óptica Austral, FOS): une tramos de la troncal "
                       "nacional por mar; el tráfico pasa por ahí hacia otras regiones.",
        "RECIBE": "Aterrizaje terminal en zona aislada: extremo de un cable que solo entrega conectividad a una "
                  "localidad (no sigue hacia otro lado).",
    },
    "IXP / PIT": {
        "INTERCAMBIA": "Punto de intercambio de tráfico: las redes de distintos operadores se interconectan. Se "
                       "incluyen todos los IXP chilenos de PeeringDB, ubicados en el data center que los aloja.",
    },
    "Data center": {
        "GENERA": "Data center que aloja servidores, nubes y cachés CDN (origen del contenido). Criterio: ≥ 9 redes "
                  "conectadas en PeeringDB, o aloja un IXP, o está junto a un aterrizaje internacional.",
    },
    "Nodo troncal": {
        "INTERCAMBIA": "Nodo de la propia red con grado ≥ 3 y mayor centralidad de intermediación (betweenness, "
                       "Freeman 1977): los 5 mayores de cada componente principal, separados ≥ 100 km.",
    },
}

TIPO_ENLACE = {
    "FIBRA": "Fibra óptica detectada en terreno (Estudio NIC Chile 2018).",
    "POSTE": "Postación eléctrica utilizable para tender cable aéreo.",
    "CAMINO": "Camino sin postación: ruta posible de despliegue (requiere obra civil).",
    "FERRY": "Trayecto en ferry: conexión marítima o cruce fluvial.",
    "CONECTOR": "Enlace sintético agregado en el apartado 2.0 para unir componentes (no viene en los datos).",
}


# -----------------------------------------------------------------------------
# Datos complementarios desde CSV (se cargan una vez)
# -----------------------------------------------------------------------------
@lru_cache
def meta_nodos():
    """Metadata de los nodos clave: fuente, URL, detalle (1.0) y confiabilidad pre/post (5.0)."""
    base = {r["nombre"]: r for r in leer_csv("nodos_clave.csv")}
    post = {r["nombre"]: r for r in leer_csv("attr_post/nodos_clave_afectados.csv")}
    return base, post


@lru_cache
def meta_criticos():
    return {int(r["id"]): r for r in leer_csv("attr_post/enlaces_criticos.csv")}


@lru_cache
def nombres_componentes():
    return {r["componente"]: r["nombre"] for r in filas("SELECT componente, nombre FROM resumen_componentes")}


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/resumen")
def resumen():
    tipos = filas("""
        SELECT codigo_tipo AS tipo, count(*) AS enlaces, round(sum(longitud_km)::numeric, 1) AS km,
               round(avg(prob_falla_8_8)::numeric, 4) AS p_media,
               round(sum(prob_falla_8_8)::numeric, 1) AS cortes_esperados
        FROM red GROUP BY 1 ORDER BY 3 DESC""")
    totales = filas("""
        SELECT count(*) AS enlaces, round(sum(longitud_km)::numeric, 2) AS km,
               (SELECT count(*) FROM red_vertices_pgr) AS vertices,
               (SELECT count(*) FROM nodos_clave) AS nodos_clave,
               count(*) FILTER (WHERE sintetico) AS sinteticos,
               count(DISTINCT componente) AS componentes,
               round(sum(prob_falla_8_8)::numeric, 1) AS cortes_esperados,
               round(max(prob_falla_8_8)::numeric, 4) AS p_max
        FROM red""")[0]
    attr = filas("SELECT escenario, metodo, attr, ic95_inf, ic95_sup, iteraciones FROM attr_resumen ORDER BY calculado")
    componentes = filas("SELECT n, nombre, componente, vertices, aristas, km, km_fibra, tipos, lat_norte, lat_sur "
                        "FROM resumen_componentes ORDER BY n")
    return {"totales": totales, "tipos": tipos, "attr": attr, "componentes": componentes,
            "attr_post": leer_csv("attr_post/attr_post_resumen.csv")}


@app.get("/api/red")
def red():
    """Todos los enlaces (GeoJSON simplificado a 20 m), con lo necesario para filtrar y colorear."""
    return json_crudo(_red_geojson())


@lru_cache
def _red_geojson():
    criticos = meta_criticos()
    texto = valor("""
        SELECT json_build_object('type', 'FeatureCollection', 'features', json_agg(json_build_object(
            'type', 'Feature', 'id', id,
            'geometry', ST_AsGeoJSON(ST_Transform(ST_Simplify(geom, 20, true), 4326), 5)::json,
            'properties', json_build_object(
                'id', id, 'tipo', codigo_tipo, 'km', round(longitud_km::numeric, 3),
                'comp', componente, 'sint', sintetico,
                'p0', prob_falla, 'p', round(prob_falla_8_8::numeric, 4),
                'pga', round(pga_g::numeric, 3), 'drup', round(dist_ruptura_km::numeric, 1)))))::text
        FROM red""")
    fc = json.loads(texto)
    comps = nombres_componentes()
    for f in fc["features"]:
        pr = f["properties"]
        pr["comp_nombre"] = comps.get(pr["comp"], f"#{pr['comp']}")
        c = criticos.get(pr["id"])
        pr["puente"] = c is not None
        pr["perdida"] = num(c["perdida_esperada_attr"]) if c else 0
    return json.dumps(fc)


@app.get("/api/red/{id_enlace}")
def enlace(id_enlace: int):
    r = filas("""
        SELECT r.id, r.id_red, r.sub_id, r.tipo_tramo, r.codigo_tipo, r.capa_origen, r.id_origen,
               r.longitud_km, r.source, r.target, r.sintetico, r.justificacion, r.componente,
               r.prob_falla, r.prob_falla_8_8, r.dist_epicentro_km, r.dist_ruptura_km, r.pga_g, r.pgv_cms,
               r.tasa_cortes_km, f.modelo, f.k, f.justificacion AS justificacion_k,
               vs.cnt AS grado_source, vt.cnt AS grado_target,
               ST_Y(ST_Transform(ST_LineInterpolatePoint(r.geom, 0.5), 4326)) AS lat,
               ST_X(ST_Transform(ST_LineInterpolatePoint(r.geom, 0.5), 4326)) AS lon
        FROM red r
        LEFT JOIN fragilidad_tipo f ON f.codigo_tipo = r.codigo_tipo
        LEFT JOIN red_vertices_pgr vs ON vs.id = r.source
        LEFT JOIN red_vertices_pgr vt ON vt.id = r.target
        WHERE r.id = %s""", (id_enlace,))
    if not r:
        raise HTTPException(404, "Enlace no encontrado")
    e = r[0]
    e["componente_nombre"] = nombres_componentes().get(e["componente"])
    e["descripcion_tipo"] = TIPO_ENLACE.get(e["codigo_tipo"])
    e["critico"] = meta_criticos().get(id_enlace)
    e["nodos_clave_extremos"] = filas(
        "SELECT nombre, tipo, rol, id_vertice FROM nodo_clave_vertice WHERE id_vertice IN (%s, %s)",
        (e["source"], e["target"]))
    return e


@app.get("/api/vertices")
def vertices():
    return json_crudo(_vertices_geojson())


@lru_cache
def _vertices_geojson():
    return valor("""
        SELECT json_build_object('type', 'FeatureCollection', 'features', json_agg(json_build_object(
            'type', 'Feature', 'id', v.id,
            'geometry', ST_AsGeoJSON(ST_Transform(v.the_geom, 4326), 5)::json,
            'properties', json_build_object('id', v.id, 'grado', v.cnt, 'comp', v.componente,
                                            'pga', round(s.pga_g::numeric, 3)))))::text
        FROM red_vertices_pgr v LEFT JOIN vertice_sismo s ON s.id = v.id""")


@app.get("/api/vertices/{id_vertice}")
def vertice(id_vertice: int):
    r = filas("""
        SELECT v.id, v.cnt AS grado, v.chk, v.ein, v.eout, v.componente,
               s.dist_epicentro_km, s.dist_ruptura_km, s.pga_g,
               ST_Y(ST_Transform(v.the_geom, 4326)) AS lat, ST_X(ST_Transform(v.the_geom, 4326)) AS lon
        FROM red_vertices_pgr v LEFT JOIN vertice_sismo s ON s.id = v.id WHERE v.id = %s""", (id_vertice,))
    if not r:
        raise HTTPException(404, "Vértice no encontrado")
    v = r[0]
    v["componente_nombre"] = nombres_componentes().get(v["componente"])
    v["enlaces"] = filas("""SELECT id, codigo_tipo, round(longitud_km::numeric, 2) AS km,
                                   round(prob_falla_8_8::numeric, 4) AS p
                            FROM red WHERE source = %s OR target = %s ORDER BY id""", (id_vertice, id_vertice))
    v["nodos_clave"] = filas("SELECT nombre, tipo, rol FROM nodo_clave_vertice WHERE id_vertice = %s", (id_vertice,))
    return v


@app.get("/api/nodos_clave")
def nodos_clave():
    base, post = meta_nodos()
    rs = filas("""
        SELECT n.nombre, n.tipo, n.rol, n.id_vertice, n.grado_vertice, n.componente, n.dist_km,
               s.pga_g, s.dist_ruptura_km,
               ST_Y(ST_Transform(n.geom, 4326)) AS lat, ST_X(ST_Transform(n.geom, 4326)) AS lon,
               ST_Y(ST_Transform(v.the_geom, 4326)) AS v_lat, ST_X(ST_Transform(v.the_geom, 4326)) AS v_lon
        FROM nodo_clave_vertice n
        LEFT JOIN red_vertices_pgr v ON v.id = n.id_vertice
        LEFT JOIN vertice_sismo s ON s.id = n.id_vertice
        ORDER BY n.nombre""")
    feats = []
    for i, r in enumerate(rs):
        b = base.get(r["nombre"], {})
        p = post.get(r["nombre"], {})
        props = dict(r)
        props.update({
            "id": i,
            "detalle": b.get("detalle"),
            "fuente": b.get("fuente"),
            "url_fuente": b.get("url_fuente"),
            "lat_fuente": num(b.get("lat_fuente")),
            "lon_fuente": num(b.get("lon_fuente")),
            "desplaz_costa_km": num(b.get("desplaz_costa_km")),
            "nodo_python": b.get("nodo_topologia"),
            "rank_intermediacion": b.get("rank_intermed_nodo"),
            "lugar": p.get("lugar"),
            "confiab_previa": num(p.get("confiab_previa")),
            "confiab_post": num(p.get("confiab_post")),
            "perdida_pct": num(p.get("perdida_pct")),
            "criterio": CRITERIO_NODO.get(r["tipo"], {}).get(r["rol"]),
            "dist_km": num(r["dist_km"]),
        })
        feats.append({"type": "Feature", "id": i,
                      "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                      "properties": props})
    return {"type": "FeatureCollection", "features": feats}


@app.get("/api/terremoto")
def terremoto():
    ev = filas("""
        SELECT id, titulo, lugar, magnitud, tipo_magnitud, fecha_utc,
               fecha_utc AT TIME ZONE 'America/Santiago' AS fecha_chile, profundidad_km, estado, tsunami,
               significancia, mmi_max, cdi_max, reportes_sentido, alerta, red, url_evento, url_geojson,
               actualizado_utc, ST_Y(geom) AS lat, ST_X(geom) AS lon
        FROM terremoto LIMIT 1""")
    if not ev:
        raise HTTPException(404, "No hay sismo cargado (correr scripts/terremoto.py)")

    # Proyección en superficie del plano de ruptura (misma geometría que f_dist_ruptura_km en 4.1)
    ruptura = valor("""
        WITH p AS (
            SELECT ST_Transform(t.geom, 32719) AS epi,
                   radians(mp('rumbo')) AS th, radians(mp('manteo')) AS dl,
                   mp('largo_al_norte') AS ln_, mp('largo_al_sur') AS ls,
                   mp('ancho_sobre_hipo') AS ws, mp('ancho_bajo_hipo') AS wb
            FROM terremoto t LIMIT 1),
        esq AS (
            SELECT k, a, c FROM p, LATERAL (VALUES
                (1, -ls, -ws * cos(dl)), (2, ln_, -ws * cos(dl)),
                (3, ln_,  wb * cos(dl)), (4, -ls,  wb * cos(dl)), (5, -ls, -ws * cos(dl))) v(k, a, c))
        SELECT ST_AsGeoJSON(ST_Transform(ST_MakePolygon(ST_MakeLine(
                   ST_SetSRID(ST_MakePoint(ST_X(epi) + 1000 * (a * sin(th) + c * cos(th)),
                                           ST_Y(epi) + 1000 * (a * cos(th) - c * sin(th))), 32719)
                   ORDER BY k)), 4326), 4)
        FROM p, esq GROUP BY epi""")

    def contornos(tabla):
        return json.loads(valor(f"""
            SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
                'type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 4)::json,
                'properties', json_build_object('valor', valor, 'unidad', unidad, 'color', color))), '[]'))::text
            FROM {tabla}"""))

    estaciones = json.loads(valor("""
        SELECT json_build_object('type', 'FeatureCollection', 'features', json_agg(json_build_object(
            'type', 'Feature', 'geometry', ST_AsGeoJSON(geom, 5)::json,
            'properties', json_build_object('estacion', estacion, 'pga_obs_g', pga_obs_g, 'fuente', fuente,
                                            'dist_ruptura_km', round(f_dist_ruptura_km(geom)::numeric, 1),
                                            'pga_modelo_g', round(f_pga_g(geom)::numeric, 3)))))::text
        FROM registro_27f"""))
    return {"evento": ev[0], "ruptura": json.loads(ruptura),
            "pga": contornos("terremoto_pga"), "mmi": contornos("terremoto_mmi"),
            "estaciones": estaciones,
            "descargas": filas("SELECT * FROM usgs_descargas") if valor(
                "SELECT to_regclass('usgs_descargas') IS NOT NULL") else []}


@app.get("/api/modelo")
def modelo():
    return {"parametros": filas("SELECT parametro, valor, unidad, fuente FROM modelo_parametros"),
            "fragilidad": filas("SELECT codigo_tipo, modelo, k, justificacion FROM fragilidad_tipo"),
            "tipos_enlace": TIPO_ENLACE}


@app.get("/api/criticos")
def criticos(limite: int = 30):
    return leer_csv("attr_post/enlaces_criticos.csv")[:limite]


@app.get("/api/sinteticos")
def sinteticos():
    return filas("SELECT id, regla, comp_desde, comp_hasta, round(dist_m::numeric, 1) AS dist_m, justificacion "
                 "FROM enlaces_sinteticos ORDER BY id")


@app.get("/")
def inicio():
    return FileResponse(ESTATICO / "index.html")


app.mount("/static", StaticFiles(directory=ESTATICO), name="static")
# Los mapas estáticos anteriores siguen disponibles
app.mount("/mapas", StaticFiles(directory=BASE / "mapas"), name="mapas")
