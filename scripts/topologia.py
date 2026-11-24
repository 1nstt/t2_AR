"""
=============================================================================
SECCIÓN 2.0 · TOPOLOGÍA CON pgr_createTopology
Ejecuta, en el contenedor de PostGIS + pgRouting, los SQL de sql/topologia/:
  1. Carga infraestructura_limpia.geojson y nodos_clave.geojson (EPSG:32719).
  2. 01 Corta los tramos en las uniones en T.
  3. 02 pgr_createTopology (tolerancia 5 m) + pgr_analyzeGraph.
  4. 03 Columna prob_falla = 0.
  5. 04 Componentes conexas (pgr_connectedComponents).
  6. Prueba de sensibilidad de la tolerancia (0,5 a 50 m).
  7. 05 Enlaces sintéticos y nueva topología + conectividad.
  8. 06 Asocia los nodos clave al vértice más cercano.
  9. Exporta tablas (CSV/GeoJSON) y el mapa mapas/mapa_topologia.html.
Uso:  docker compose run --rm app python scripts/topologia.py
=============================================================================
"""
import json
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import Fullscreen

from db import DIRECTORIO_BASE, conexion, consulta, ejecutar_sql, motor

SALIDA = DIRECTORIO_BASE / "datos" / "salida"
SALIDA_TOPO = SALIDA / "topologia"
RUTA_MAPA = DIRECTORIO_BASE / "mapas" / "mapa_topologia.html"
CRS_METROS = 32719  # WGS 84 / UTM 19S
TOLERANCIAS = [0.5, 1, 2, 5, 10, 20, 50]

pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 60)


def titulo(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def cargar_datos(eng):
    titulo("1. Carga de la red y los nodos clave a PostGIS (EPSG:32719)")
    red = gpd.read_file(SALIDA / "infraestructura_limpia.geojson").to_crs(CRS_METROS)
    red.rename_geometry("geom").to_postgis("infra_original", eng, if_exists="replace", index=False)
    claves = gpd.read_file(SALIDA / "nodos_clave.geojson").to_crs(CRS_METROS)
    claves[["nombre", "tipo", "rol", "geometry"]].rename_geometry("geom").to_postgis(
        "nodos_clave", eng, if_exists="replace", index=False)
    print(f"   infra_original: {len(red)} tramos | nodos_clave: {len(claves)} puntos")


def estado(con, etapa):
    """Fila resumen de la topología en un momento dado."""
    a = consulta(con, """SELECT COUNT(*) aristas, COUNT(*) FILTER (WHERE sintetico) sinteticas,
                                ROUND(SUM(longitud_km)::numeric, 2) km FROM red""").iloc[0]
    v = consulta(con, """SELECT COUNT(*) vertices, COUNT(*) FILTER (WHERE cnt = 1) grado_1,
                                COUNT(*) FILTER (WHERE cnt = 2) grado_2,
                                COUNT(*) FILTER (WHERE cnt >= 3) grado_3_o_mas,
                                COUNT(DISTINCT componente) componentes FROM red_vertices_pgr""").iloc[0]
    c1 = consulta(con, "SELECT km FROM resumen_componentes WHERE n = 1").iloc[0, 0]
    return {"etapa": etapa, **a.to_dict(), **v.to_dict(),
            "km_componente_mayor": float(c1),
            "pct_km_componente_mayor": round(100 * float(c1) / float(a["km"]), 1)}


def distancias_componentes(con):
    """Distancia mínima de cada componente a un tramo de cualquier otra componente
    (vértice → arista, búsqueda hasta 50 km). NULL = nada a menos de 50 km."""
    return consulta(con, """
        SELECT a.nombre AS componente, x.componente_cercana,
               ROUND(x.d::numeric, 1) AS dist_m, x.id_vertice, x.grado_vertice, x.id_arista, x.tipo_arista,
               ROUND(ST_Y(ST_Transform(x.vg, 4326))::numeric, 5) AS lat,
               ROUND(ST_X(ST_Transform(x.vg, 4326))::numeric, 5) AS lon
        FROM resumen_componentes a
        LEFT JOIN LATERAL (
            SELECT rc2.nombre AS componente_cercana, ST_Distance(v.the_geom, e.geom) AS d, v.id AS id_vertice,
                   v.cnt AS grado_vertice, e.id AS id_arista, e.codigo_tipo AS tipo_arista, v.the_geom AS vg
            FROM red_vertices_pgr v
            JOIN red e ON e.componente <> v.componente AND ST_DWithin(v.the_geom, e.geom, 50000)
            JOIN resumen_componentes rc2 ON rc2.componente = e.componente
            WHERE v.componente = a.componente
            ORDER BY v.the_geom <-> e.geom LIMIT 1
        ) x ON true
        ORDER BY a.n""")


def sensibilidad(con):
    titulo("6. Sensibilidad de la tolerancia de pgr_createTopology")
    filas = []
    for tol in TOLERANCIAS:
        con.execute("DROP TABLE IF EXISTS sens; DROP TABLE IF EXISTS sens_vertices_pgr;")
        con.execute("CREATE TABLE sens AS SELECT id, geom, longitud_km, "
                    "NULL::bigint source, NULL::bigint target FROM red_base")
        con.execute("SELECT pgr_createTopology('sens', %s, 'geom', 'id', clean := true)", (tol,))
        r = consulta(con, """
            WITH cc AS (SELECT * FROM pgr_connectedComponents(
                'SELECT id, source, target, 1 AS cost, 1 AS reverse_cost FROM sens WHERE source <> target'))
            SELECT (SELECT COUNT(*) FROM sens_vertices_pgr) vertices,
                   (SELECT COUNT(*) FROM sens WHERE source = target) lazos,
                   (SELECT COUNT(DISTINCT component) FROM cc) componentes""").iloc[0]
        filas.append({"tolerancia_m": tol, **r.to_dict()})
    con.execute("DROP TABLE IF EXISTS sens; DROP TABLE IF EXISTS sens_vertices_pgr;")
    df = pd.DataFrame(filas)
    print(df.to_string(index=False))
    return df


def exportar(con, eng, tablas):
    titulo("9. Exportación de tablas")
    SALIDA_TOPO.mkdir(parents=True, exist_ok=True)
    for nombre, df in tablas.items():
        df.to_csv(SALIDA_TOPO / f"{nombre}.csv", index=False, encoding="utf-8-sig")
        print(f"   datos/salida/topologia/{nombre}.csv ({len(df)} filas)")
    capas = {
        "red_topologia": "SELECT id, id_red, sub_id, tipo_tramo, codigo_tipo, capa_origen, id_origen, "
                         "longitud_km, source, target, prob_falla, sintetico, justificacion, "
                         "c.nombre AS componente, geom FROM red r "
                         "LEFT JOIN resumen_componentes c ON c.componente = r.componente",
        "vertices_topologia": "SELECT v.id, v.cnt AS grado, v.chk, c.nombre AS componente, v.the_geom AS geom "
                              "FROM red_vertices_pgr v LEFT JOIN resumen_componentes c "
                              "ON c.componente = v.componente",
        "enlaces_sinteticos": "SELECT * FROM enlaces_sinteticos",
    }
    gdfs = {}
    for nombre, sql in capas.items():
        g = gpd.read_postgis(sql, eng, geom_col="geom").to_crs(4326).rename_geometry("geometry")
        g.to_file(SALIDA_TOPO / f"{nombre}.geojson", driver="GeoJSON", encoding="utf-8")
        gdfs[nombre] = g
        print(f"   datos/salida/topologia/{nombre}.geojson ({len(g)} elementos)")
    return gdfs


# -----------------------------------------------------------------------------
# MAPA
# -----------------------------------------------------------------------------
COLORES_COMP = {"C1": "#1d4ed8", "C2": "#d9480f", "C3": "#2b8a3e", "C4": "#ae3ec9", "C5": "#e8590c"}
COLOR_MENOR = "#868e96"
COLOR_SINTETICO = "#e64980"


def mapa(gdfs, nodos_clave):
    titulo("10. Mapa de componentes")
    red = gdfs["red_topologia"].copy()
    simple = red.geometry.simplify(0.0002, preserve_topology=True)  # ~20 m, solo para aligerar el HTML
    red["geometry"] = simple.where(simple.notna() & ~simple.is_empty, red.geometry)
    red = red[red.geometry.notna() & ~red.geometry.is_empty]
    vert = gdfs["vertices_topologia"]
    m = folium.Map(location=[-37.5, -71.5], zoom_start=5, tiles="OpenStreetMap", prefer_canvas=True)
    Fullscreen().add_to(m)

    principales = list(COLORES_COMP)
    for comp, color in COLORES_COMP.items():
        sub = red[(red["componente"] == comp) & (~red["sintetico"])]
        if sub.empty:
            continue
        km = sub["longitud_km"].sum()
        folium.GeoJson(
            sub[["id", "codigo_tipo", "longitud_km", "componente", "geometry"]],
            name=f"{comp} · {km:,.0f} km",
            style_function=lambda f, c=color: {"color": c, "weight": 2.2, "opacity": 0.85},
            tooltip=folium.GeoJsonTooltip(["id", "componente", "codigo_tipo", "longitud_km"]),
        ).add_to(m)
    menores = red[(~red["componente"].isin(principales)) & (~red["sintetico"])]
    if not menores.empty:
        folium.GeoJson(
            menores[["id", "codigo_tipo", "longitud_km", "componente", "geometry"]],
            name=f"Componentes menores ({menores['componente'].nunique()})",
            style_function=lambda f: {"color": COLOR_MENOR, "weight": 3, "opacity": 0.9},
            tooltip=folium.GeoJsonTooltip(["id", "componente", "codigo_tipo", "longitud_km"]),
        ).add_to(m)

    sint = gdfs["enlaces_sinteticos"]
    fg = folium.FeatureGroup(name=f"Enlaces sintéticos ({len(sint)})")
    for r in sint.itertuples():
        coords = [(y, x) for x, y in r.geometry.coords]
        folium.PolyLine(coords, color=COLOR_SINTETICO, weight=6, dash_array="8 6",
                        tooltip=f"S{r.id} · {r.regla} · {r.comp_desde}→{r.comp_hasta} · {r.dist_m:.0f} m",
                        popup=folium.Popup(r.justificacion, max_width=320)).add_to(fg)
        folium.CircleMarker(coords[0], radius=6, color=COLOR_SINTETICO, fill=True).add_to(fg)
    fg.add_to(m)

    fg = folium.FeatureGroup(name=f"Extremos libres, grado 1 ({(vert['grado'] == 1).sum()})", show=False)
    for r in vert[vert["grado"] == 1].itertuples():
        folium.CircleMarker((r.geometry.y, r.geometry.x), radius=3, color="#212529", weight=1,
                            fill=True, fill_opacity=0.8,
                            tooltip=f"Vértice {r.id} · grado 1 · {r.componente}").add_to(fg)
    fg.add_to(m)

    fg = folium.FeatureGroup(name=f"Nodos clave → vértice ({len(nodos_clave)})")
    vxy = vert.set_index("id").geometry
    for r in nodos_clave.itertuples():
        p = r.geometry
        v = vxy[r.id_vertice]
        folium.PolyLine([(p.y, p.x), (v.y, v.x)], color="#f59f00", weight=1.5, dash_array="4").add_to(fg)
        folium.CircleMarker((p.y, p.x), radius=6, color="#7c4a03", weight=1.5,
                            fill=True, fill_color="#f59f00", fill_opacity=1,
                            tooltip=f"{r.nombre} → vértice {r.id_vertice} ({r.componente}, "
                                            f"{r.dist_km} km)").add_to(fg)
    fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element("""
    <div style="position:fixed;bottom:18px;left:12px;z-index:9999;background:#fff;padding:8px 10px;
         border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.3);font:12px/1.4 sans-serif;max-width:270px">
      <b>Topología pgr_createTopology (tolerancia 5 m)</b><br>
      Color = componente conexa. Rosado punteado = enlace sintético (clic para la justificación).
      Naranjo = nodo clave unido a su vértice más cercano.
    </div>"""))
    RUTA_MAPA.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(RUTA_MAPA))
    print(f"   mapas/{RUTA_MAPA.name} ({RUTA_MAPA.stat().st_size / 1e6:.1f} MB)")


def main():
    eng, con = motor(), conexion()
    titulo("0. Versiones del entorno")
    print(consulta(con, "SELECT version() AS postgresql, postgis_lib_version() AS postgis, "
                        "pgr_version() AS pgrouting").T.to_string(header=False))
    cargar_datos(eng)

    titulo("2-5. Corte en uniones en T, pgr_createTopology, prob_falla y conectividad")
    for f in ["01_red_cortada.sql", "02_create_topology.sql", "03_prob_falla.sql", "04_conectividad.sql"]:
        ejecutar_sql(con, f)
    con.execute("DROP TABLE IF EXISTS red_base; CREATE TABLE red_base AS SELECT * FROM red;")

    cortes = consulta(con, """SELECT (SELECT COUNT(*) FROM uniones_t) uniones_t_detectadas,
                                     (SELECT COUNT(*) FROM cortes) puntos_de_corte,
                                     (SELECT COUNT(DISTINCT id_tramo) FROM cortes) tramos_cortados,
                                     (SELECT COUNT(*) FROM infra_original) tramos_originales,
                                     (SELECT COUNT(*) FROM red_descartada) lazos_descartados""")
    print(cortes.to_string(index=False))
    estados = [estado(con, "antes de enlaces sintéticos")]
    comp_antes = consulta(con, "SELECT * FROM resumen_componentes ORDER BY n")
    print(comp_antes.to_string(index=False))
    dist_antes = distancias_componentes(con)
    print(dist_antes.to_string(index=False))

    sens = sensibilidad(con)

    titulo("7. Enlaces sintéticos y nueva topología")
    for f in ["05_enlaces_sinteticos.sql", "02_create_topology.sql", "04_conectividad.sql"]:
        ejecutar_sql(con, f)
    sint = consulta(con, """SELECT id, regla, comp_desde, comp_hasta, ROUND(dist_m::numeric, 1) dist_m,
               ROUND(ST_Y(ST_Transform(ST_StartPoint(geom), 4326))::numeric, 5) lat,
               ROUND(ST_X(ST_Transform(ST_StartPoint(geom), 4326))::numeric, 5) lon, justificacion
        FROM enlaces_sinteticos ORDER BY id""")
    print(sint.to_string(index=False))
    estados.append(estado(con, "después de enlaces sintéticos"))
    comp_despues = consulta(con, "SELECT * FROM resumen_componentes ORDER BY n")
    print(comp_despues.to_string(index=False))
    dist_despues = distancias_componentes(con)
    print(dist_despues.to_string(index=False))

    titulo("8. Nodos clave → vértice más cercano")
    ejecutar_sql(con, "06_nodos_clave.sql")
    claves = consulta(con, "SELECT nombre, tipo, rol, id_vertice, grado_vertice, componente, dist_km "
                           "FROM nodo_clave_vertice ORDER BY componente, dist_km DESC")
    print(claves.to_string(index=False))

    # Comparación con el grafo de Python (nodos_clave.py): mismo nodo y misma distancia
    py = pd.read_csv(SALIDA / "nodos_clave.csv")[["nombre", "nodo_topologia", "dist_nodo_km", "grado_nodo", "componente"]]
    comp = py.rename(columns={"componente": "componente_python"}).merge(
        claves[["nombre", "id_vertice", "dist_km", "grado_vertice", "componente"]]
        .rename(columns={"componente": "componente_pgrouting"}), on="nombre")
    comp["dif_dist_km"] = (comp["dist_km"].astype(float) - comp["dist_nodo_km"]).round(2)
    comp["mismo_grado"] = comp["grado_nodo"] == comp["grado_vertice"]
    print(comp.to_string(index=False))

    # Tablas de resultados
    estados = pd.DataFrame(estados)
    print("\n", estados.T.to_string())
    vertices_muestra = consulta(con, """SELECT v.id, v.cnt, v.chk, v.ein, v.eout, c.nombre componente,
            ROUND(ST_X(ST_Transform(v.the_geom, 4326))::numeric, 6) lon,
            ROUND(ST_Y(ST_Transform(v.the_geom, 4326))::numeric, 6) lat
        FROM red_vertices_pgr v JOIN resumen_componentes c USING (componente) ORDER BY v.id LIMIT 10""")
    aristas_muestra = consulta(con, """SELECT id, id_red, sub_id, codigo_tipo, ROUND(longitud_km::numeric, 3) km,
            source, target, prob_falla, sintetico FROM red ORDER BY id LIMIT 10""")
    prob = consulta(con, """SELECT COUNT(*) aristas, MIN(prob_falla) min, MAX(prob_falla) max,
            COUNT(*) FILTER (WHERE prob_falla = 0) en_cero FROM red""")
    km_tipo = consulta(con, """SELECT codigo_tipo, COUNT(*) aristas, ROUND(SUM(longitud_km)::numeric, 2) km
            FROM red GROUP BY codigo_tipo ORDER BY km DESC""")
    grados = consulta(con, "SELECT cnt AS grado, COUNT(*) vertices FROM red_vertices_pgr GROUP BY cnt ORDER BY cnt")
    descartadas = consulta(con, "SELECT id, id_red, sub_id, codigo_tipo, ROUND((longitud_km*1000)::numeric, 1) "
                                "largo_m FROM red_descartada")
    print(vertices_muestra.to_string(index=False), aristas_muestra.to_string(index=False),
          prob.to_string(index=False), km_tipo.to_string(index=False), grados.to_string(index=False),
          descartadas.to_string(index=False), sep="\n\n")

    gdfs = exportar(con, eng, {
        "resumen_etapas": estados, "cortes_uniones_t": cortes, "sensibilidad_tolerancia": sens,
        "componentes_antes": comp_antes, "componentes_despues": comp_despues,
        "distancias_componentes_antes": dist_antes, "distancias_componentes_despues": dist_despues,
        "enlaces_sinteticos": sint, "nodos_clave_vertice": claves, "km_por_tipo": km_tipo,
        "grados_vertices": grados, "aristas_descartadas": descartadas,
        "muestra_vertices": vertices_muestra, "muestra_aristas": aristas_muestra,
        "prob_falla_resumen": prob, "comparacion_grafo_python": comp,
    })
    nodos = (gpd.read_postgis("SELECT * FROM nodo_clave_vertice", eng, geom_col="geom")
             .to_crs(4326).rename_geometry("geometry"))
    mapa(gdfs, nodos)


class _Tee:
    """Escribe la salida en pantalla y en el log de ejecución."""
    def __init__(self, *flujos):
        self.flujos = flujos

    def write(self, s):
        for f in self.flujos:
            f.write(s)

    def flush(self):
        for f in self.flujos:
            f.flush()


if __name__ == "__main__":
    import sys
    SALIDA_TOPO.mkdir(parents=True, exist_ok=True)
    with open(SALIDA_TOPO / "ejecucion_topologia.log", "w", encoding="utf-8") as log:
        sys.stdout = _Tee(sys.__stdout__, log)
        try:
            main()
        finally:
            sys.stdout = sys.__stdout__
