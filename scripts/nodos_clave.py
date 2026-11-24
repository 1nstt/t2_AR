"""
=============================================================================
PROYECTO: Análisis de Resiliencia de Infraestructura de Internet en Chile
SCRIPT:   nodos_clave.py
DESCRIPCIÓN:
    Lista los nodos clave de la red (aterrizajes de cables submarinos, IXP/PIT,
    data centers y nodos troncales) con su ubicación, su rol (GENERA /
    INTERCAMBIA / RECIBE), el criterio y la fuente que lo justifica, y los
    ubica en la topología construida por 'main.py'
    (nodo N-xxxx más cercano, distancia, grado y centralidad).

ENTRADAS:  datos/salida/infraestructura_limpia.geojson, datos/salida/nodos_clasificados.geojson
SALIDAS:   datos/salida/nodos_clave.csv, datos/salida/nodos_clave.geojson,
           mapas/mapa_nodos_clave.html (mismo visualizador de main.py + capas y filtros de nodos clave)
=============================================================================
"""

import json
import math

import folium
import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import Point
from shapely.strtree import STRtree

import main as mapa_base  # mismo pipeline y visualizador de mapa_infraestructura.html

BASE = mapa_base.DIRECTORIO_BASE
RUTA_LINEAS = mapa_base.RUTA_GEOJSON_LINEAS
RUTA_NODOS = mapa_base.RUTA_GEOJSON_NODOS
RUTA_CSV = mapa_base.DIRECTORIO_SALIDA / "nodos_clave.csv"
RUTA_GEOJSON = mapa_base.DIRECTORIO_SALIDA / "nodos_clave.geojson"
RUTA_HTML = mapa_base.DIRECTORIO_MAPAS / "mapa_nodos_clave.html"

# -----------------------------------------------------------------------------
# FUENTES (consultadas el 21-11-2026)
# -----------------------------------------------------------------------------
F_TG = "TeleGeography Submarine Cable Map (submarinecablemap.com/api/v3/landing-point)"
F_PDB_IX = "PeeringDB API /api/ix?country=CL y /api/ixfac"
F_PDB_FAC = "PeeringDB API /api/fac?country=CL"
F_TOPO = "Topología propia (main.py) + centralidad de intermediación (Freeman, 1977)"

# -----------------------------------------------------------------------------
# INVENTARIO DE NODOS CLAVE CON FUENTE
# (lon, lat) tal como los publica la fuente. Los DC de Telxius no tienen
# coordenadas en PeeringDB: se usa la del aterrizaje del mismo operador.
# -----------------------------------------------------------------------------
INVENTARIO = [
    # --- Aterrizajes de cables submarinos INTERNACIONALES -> GENERAN ---------
    ("CLS Arica", "Aterrizaje cable submarino", "GENERA", -70.30675, -18.47337,
     "Cables internacionales: SAm-1, SPCS/Mistral (+ Prat doméstico)", F_TG),
    ("CLS Valparaíso", "Aterrizaje cable submarino", "GENERA", -71.62048, -33.04577,
     "Cables internacionales: Curie, SAm-1, SAC, SPCS/Mistral (+ Prat)", F_TG),
    # --- Aterrizajes DOMÉSTICOS (troncal submarina) -> INTERCAMBIAN ----------
    ("CLS Iquique", "Aterrizaje cable submarino", "INTERCAMBIA", -70.14222, -20.21670, "Cable Prat (troncal doméstica)", F_TG),
    ("CLS Tocopilla", "Aterrizaje cable submarino", "INTERCAMBIA", -70.19766, -22.08838, "Cable Prat", F_TG),
    ("CLS Antofagasta", "Aterrizaje cable submarino", "INTERCAMBIA", -70.40000, -23.65000, "Cable Prat", F_TG),
    ("CLS Caldera", "Aterrizaje cable submarino", "INTERCAMBIA", -70.81236, -27.06199, "Cable Prat", F_TG),
    ("CLS La Serena", "Aterrizaje cable submarino", "INTERCAMBIA", -71.25020, -29.90630, "Cable Prat", F_TG),
    ("CLS Cartagena", "Aterrizaje cable submarino", "INTERCAMBIA", -71.60009, -33.54688, "Cable Prat", F_TG),
    ("CLS Constitución", "Aterrizaje cable submarino", "INTERCAMBIA", -72.41607, -35.31837, "Cable Prat", F_TG),
    ("CLS San Pedro de la Paz", "Aterrizaje cable submarino", "INTERCAMBIA", -73.10897, -36.83168, "Cable Prat", F_TG),
    ("CLS Puerto Saavedra", "Aterrizaje cable submarino", "INTERCAMBIA", -73.39677, -38.78713, "Cable Prat", F_TG),
    ("CLS Puerto Montt", "Aterrizaje cable submarino", "INTERCAMBIA", -72.95341, -41.46037,
     "Cables Prat + Fibra Óptica Austral (une troncal norte con la austral)", F_TG),
    ("CLS Punta Arenas", "Aterrizaje cable submarino", "INTERCAMBIA", -70.93333, -53.16666,
     "Fibra Óptica Austral (hub austral, también tiene IXP)", F_TG),
    ("CLS Meimen (Chacao norte)", "Aterrizaje cable submarino", "INTERCAMBIA", -73.15529, -41.80692, "Segunda FOS Canal de Chacao", F_TG),
    ("CLS Linao (Chacao sur)", "Aterrizaje cable submarino", "INTERCAMBIA", -73.50446, -41.99113, "Segunda FOS Canal de Chacao", F_TG),
    ("CLS Quellón", "Aterrizaje cable submarino", "INTERCAMBIA", -73.47946, -43.11747, "FOS Quellón-Chacabuco", F_TG),
    # --- Aterrizajes TERMINALES (zonas aisladas) -> RECIBEN ------------------
    ("CLS Puerto Chacabuco", "Aterrizaje cable submarino", "RECIBE", -72.80510, -45.45830,
     "Extremo FOS Quellón-Chacabuco (entrega a Aysén)", F_TG),
    ("CLS Caleta Tortel", "Aterrizaje cable submarino", "RECIBE", -73.53689, -47.80121,
     "Derivación de Fibra Óptica Austral a localidad aislada", F_TG),
    ("CLS Puerto Williams", "Aterrizaje cable submarino", "RECIBE", -67.60586, -54.93520,
     "Extremo sur de Fibra Óptica Austral", F_TG),
    # --- IXP / PIT -> INTERCAMBIAN (ubicación = facility que los aloja) ------
    ("PIT Chile Santiago", "IXP / PIT", "INTERCAMBIA", -70.70496, -33.33732,
     "Mayor IXP de Chile (150 redes); 13 sedes, ej. Ascenty SCL02", F_PDB_IX),
    ("Chile IX by PIT.net", "IXP / PIT", "INTERCAMBIA", -70.67626, -33.35799, "20 redes; sede Cirion SAN1", F_PDB_IX),
    ("ixFabric Santiago", "IXP / PIT", "INTERCAMBIA", -70.66930, -33.44890, "14 redes (sin facility publicada; centro de Santiago)", F_PDB_IX),
    ("PIT entel Santiago", "IXP / PIT", "INTERCAMBIA", -70.65582, -33.44444, "Sede entel CNT (Torre Entel)", F_PDB_IX),
    ("MetroPort IX", "IXP / PIT", "INTERCAMBIA", -71.56745, -33.02924, "Viña del Mar; sede Tecnoera", F_PDB_IX),
    ("PIT Concepción", "IXP / PIT", "INTERCAMBIA", -73.04843, -36.81577, "7 redes; sede Opciones Concepción", F_PDB_IX),
    ("PIT Temuco", "IXP / PIT", "INTERCAMBIA", -72.59604, -38.74267, "4 redes; sede Anacondaweb", F_PDB_IX),
    ("ixFabric Valdivia", "IXP / PIT", "INTERCAMBIA", -73.24590, -39.81420, "7 redes (sin facility publicada; centro de Valdivia)", F_PDB_IX),
    ("PatagoniaIX", "IXP / PIT", "INTERCAMBIA", -70.87293, -53.04668, "7 redes; sede Apoapsis Ojo Bueno, Punta Arenas", F_PDB_IX),
    # --- DATA CENTERS -> GENERAN (alojan contenido, nubes y cachés CDN) ------
    ("Cirion SAN1", "Data center", "GENERA", -70.67626, -33.35799, "70 redes conectadas; aloja PIT Chile y Chile IX", F_PDB_FAC),
    ("Ascenty SCL02", "Data center", "GENERA", -70.70496, -33.33732, "36 redes; aloja 3 IXP", F_PDB_FAC),
    ("Ascenty SCL01", "Data center", "GENERA", -70.70621, -33.33854, "26 redes; aloja 2 IXP", F_PDB_FAC),
    ("IFX Santiago", "Data center", "GENERA", -70.59957, -33.41842, "22 redes", F_PDB_FAC),
    ("Grupo ZGH La Florida", "Data center", "GENERA", -70.57492, -33.55901, "14 redes; aloja PIT Chile", F_PDB_FAC),
    ("Equinix ST1/ST2", "Data center", "GENERA", -70.84832, -33.44802, "11 redes; aloja PIT Chile y PIT entel", F_PDB_FAC),
    ("GTD Moneda", "Data center", "GENERA", -70.64958, -33.44199, "11 redes", F_PDB_FAC),
    ("EdgeUno SCL1", "Data center", "GENERA", -70.67626, -33.35799, "11 redes", F_PDB_FAC),
    ("entel CNT (Torre Entel)", "Data center", "GENERA", -70.65582, -33.44444, "10 redes; aloja PIT Chile y PIT entel", F_PDB_FAC),
    ("PowerHost Santiago", "Data center", "GENERA", -70.61990, -33.44794, "10 redes", F_PDB_FAC),
    ("EdgeConneX EDCSCL01", "Data center", "GENERA", -70.70160, -33.29740, "9 redes; aloja PIT Chile", F_PDB_FAC),
    ("Scala SSCLCR01 (Placilla)", "Data center", "GENERA", -71.55844, -33.13578, "Aloja PIT Chile; junto al CLS Valparaíso", F_PDB_FAC),
    ("Telxius Valparaíso DC", "Data center", "GENERA", -71.62048, -33.04577, "DC del CLS (sin coord. en PeeringDB; se usa la del CLS)", F_PDB_FAC),
    ("Telxius Arica DC", "Data center", "GENERA", -70.30675, -18.47337, "DC del CLS (sin coord. en PeeringDB; se usa la del CLS)", F_PDB_FAC),
    ("Opciones Concepción", "Data center", "GENERA", -73.04843, -36.81577, "Aloja PIT Concepción", F_PDB_FAC),
    ("Anacondaweb Temuco", "Data center", "GENERA", -72.59604, -38.74267, "Aloja PIT Temuco", F_PDB_FAC),
    ("Apoapsis Ojo Bueno", "Data center", "GENERA", -70.87293, -53.04668, "Aloja PatagoniaIX", F_PDB_FAC),
]

# Enlace al registro exacto de cada nodo en su fuente (se muestra al hacer clic)
_TG = "https://www.submarinecablemap.com/landing-point/"
_IX = "https://www.peeringdb.com/ix/"
_FAC = "https://www.peeringdb.com/fac/"
URL_FUENTE = {
    "CLS Arica": _TG + "arica-chile", "CLS Valparaíso": _TG + "valparaso-chile",
    "CLS Iquique": _TG + "iquique-chile", "CLS Tocopilla": _TG + "tocopilla-chile",
    "CLS Antofagasta": _TG + "antofagasta-chile", "CLS Caldera": _TG + "caldera-chile",
    "CLS La Serena": _TG + "la-serena-chile", "CLS Cartagena": _TG + "cartagena-chile",
    "CLS Constitución": _TG + "constitucin-chile", "CLS San Pedro de la Paz": _TG + "san-pedro-de-la-paz-chile",
    "CLS Puerto Saavedra": _TG + "puerto-saavedra-chile", "CLS Puerto Montt": _TG + "puerto-montt-chile",
    "CLS Punta Arenas": _TG + "punta-arenas-chile", "CLS Meimen (Chacao norte)": _TG + "meimen-chile",
    "CLS Linao (Chacao sur)": _TG + "linao-chile", "CLS Quellón": _TG + "quellon-chile",
    "CLS Puerto Chacabuco": _TG + "puerto-chacabuco-chile", "CLS Caleta Tortel": _TG + "tortel-chile",
    "CLS Puerto Williams": _TG + "puerto-williams-chile",
    "PIT Chile Santiago": _IX + "1514", "Chile IX by PIT.net": _IX + "4820", "ixFabric Santiago": _IX + "3937",
    "PIT entel Santiago": _IX + "2176", "MetroPort IX": _IX + "5058", "PIT Concepción": _IX + "1915",
    "PIT Temuco": _IX + "1917", "ixFabric Valdivia": _IX + "3842", "PatagoniaIX": _IX + "4607",
    "Cirion SAN1": _FAC + "2093", "Ascenty SCL02": _FAC + "11683", "Ascenty SCL01": _FAC + "7594",
    "IFX Santiago": _FAC + "5360", "Grupo ZGH La Florida": _FAC + "5274", "Equinix ST1/ST2": _FAC + "5295",
    "GTD Moneda": _FAC + "6733", "EdgeUno SCL1": _FAC + "7465", "entel CNT (Torre Entel)": _FAC + "5301",
    "PowerHost Santiago": _FAC + "9202", "EdgeConneX EDCSCL01": _FAC + "11784",
    "Scala SSCLCR01 (Placilla)": _FAC + "14340", "Telxius Valparaíso DC": _FAC + "8554",
    "Telxius Arica DC": _FAC + "9087", "Opciones Concepción": _FAC + "14127",
    "Anacondaweb Temuco": _FAC + "15075", "Apoapsis Ojo Bueno": _FAC + "15789",
}
assert all(n[0] in URL_FUENTE for n in INVENTARIO), "Falta el enlace de fuente de algún nodo"

# Punto de la costa (lon, lat) donde se ubica cada aterrizaje.
# TeleGeography entrega la coordenada de la LOCALIDAD (a veces 1-4 km tierra adentro).
# Se tomó el punto más cercano de la línea de costa de OpenStreetMap
# (way natural=coastline, API Overpass, radio 15 km, sin islotes cerrados),
# consultado el 21-11-2026. Se deja fijo para que el resultado sea reproducible.
F_COSTA = "Línea de costa: OpenStreetMap (natural=coastline, vía Overpass API)"
COSTA = {
    "CLS Arica": (-70.30917, -18.46745), "CLS Valparaíso": (-71.61991, -33.04275),
    "CLS Iquique": (-70.15190, -20.20990), "CLS Tocopilla": (-70.19818, -22.08786),
    "CLS Antofagasta": (-70.40488, -23.64980), "CLS Caldera": (-70.81270, -27.06161),
    "CLS La Serena": (-71.27453, -29.90354), "CLS Cartagena": (-71.60337, -33.54466),
    "CLS Constitución": (-72.41673, -35.31760), "CLS San Pedro de la Paz": (-73.15421, -36.83106),
    "CLS Puerto Saavedra": (-73.40665, -38.78965), "CLS Puerto Montt": (-72.94546, -41.47470),
    "CLS Punta Arenas": (-70.91841, -53.17210), "CLS Meimen (Chacao norte)": (-73.15525, -41.80693),
    "CLS Linao (Chacao sur)": (-73.50412, -41.99085), "CLS Quellón": (-73.47894, -43.11741),
    "CLS Puerto Chacabuco": (-72.80510, -45.45829), "CLS Caleta Tortel": (-73.53654, -47.80180),
    "CLS Puerto Williams": (-67.60623, -54.93290),
}
assert all(n[0] in COSTA for n in INVENTARIO if n[1] == "Aterrizaje cable submarino")

# Localidades de referencia solo para nombrar los nodos troncales calculados
LOCALIDADES = {
    "Arica": (-70.3126, -18.4783), "Iquique": (-70.1503, -20.2133), "Calama": (-68.9293, -22.4544),
    "Antofagasta": (-70.3975, -23.6509), "Copiapó": (-70.3323, -27.3668), "Vallenar": (-70.7581, -28.5753),
    "La Serena": (-71.2519, -29.9027), "Ovalle": (-71.1999, -30.6015), "Los Vilos": (-71.5139, -31.9113),
    "Valparaíso": (-71.6127, -33.0472), "Santiago": (-70.6693, -33.4489), "Rancagua": (-70.7444, -34.1708),
    "San Fernando": (-70.9887, -34.5853), "Curicó": (-71.2392, -34.9828), "Talca": (-71.6554, -35.4264),
    "Linares": (-71.5967, -35.8467), "Chillán": (-72.1034, -36.6066), "Concepción": (-73.0503, -36.8270),
    "Los Ángeles": (-72.3532, -37.4697), "Angol": (-72.7166, -37.7957), "Temuco": (-72.5904, -38.7359),
    "Villarrica": (-72.2302, -39.2857), "Valdivia": (-73.2459, -39.8142), "Osorno": (-73.1336, -40.5739),
    "Puerto Montt": (-72.9424, -41.4693), "Castro": (-73.7623, -42.4800), "Chaitén": (-72.7083, -42.9167),
    "Coyhaique": (-72.0685, -45.5712), "Cochrane": (-72.5667, -47.2539), "Puerto Natales": (-72.5064, -51.7236),
    "Punta Arenas": (-70.9171, -53.1638), "Porvenir": (-70.3700, -53.2958),
    "Taltal": (-70.4867, -25.4053), "Chañaral": (-70.6167, -26.3479), "Traiguén": (-72.6667, -38.2500),
    "Teodoro Schmidt": (-73.0917, -39.0042), "Río Bueno": (-72.9553, -40.3347),
}


def haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def localidad_cercana(lon, lat):
    nom, (lo, la) = min(LOCALIDADES.items(), key=lambda kv: haversine_km(lon, lat, *kv[1]))
    return nom, haversine_km(lon, lat, lo, la)


def construir_grafo(gdf_lineas, gdf_nodos):
    """Grafo no dirigido: cada tramo se parte en todos los nodos que caen sobre él
    (extremos + uniones en T), con peso = km proporcional a la posición."""
    G = nx.Graph()
    coord_a_id = {(round(r.lon, 6), round(r.lat, 6)): r.etiqueta for r in gdf_nodos.itertuples()}
    pts = list(gdf_nodos.geometry.values)
    etiquetas = gdf_nodos["etiqueta"].tolist()
    arbol = STRtree(pts)
    for fila in gdf_lineas.itertuples():
        geom = fila.geometry
        en_linea = []
        for i in arbol.query(geom.buffer(0.000005)):
            if pts[i].distance(geom) < 0.000002:
                en_linea.append((geom.project(pts[i], normalized=True), etiquetas[i]))
        c = list(geom.coords)
        for extremo, frac in ((c[0], 0.0), (c[-1], 1.0)):
            et = coord_a_id.get((round(extremo[0], 6), round(extremo[1], 6)))
            if et:
                en_linea.append((frac, et))
        en_linea = sorted(set(en_linea))
        for (f1, a), (f2, b) in zip(en_linea, en_linea[1:]):
            if a != b:
                w = max((f2 - f1) * fila.longitud_km, 1e-6)
                if not G.has_edge(a, b) or G[a][b]["weight"] > w:
                    G.add_edge(a, b, weight=w)
    return G


def main():
    print("-> Cargando topología...")
    gdf_lineas = mapa_base.aplicar_snapping_topologico(gpd.read_file(RUTA_LINEAS), tolerancia_metros=5.0)
    gdf_nodos = gpd.read_file(RUTA_NODOS)

    print("-> Construyendo grafo y calculando centralidad de intermediación...")
    G = construir_grafo(gdf_lineas, gdf_nodos)
    # La red de entrada NO es un único grafo conexo: se calcula la intermediación
    # dentro de cada componente y se nombran los componentes por su latitud.
    componentes = sorted(nx.connected_components(G), key=len, reverse=True)
    btw, comp_de = {}, {}
    lat_de = dict(zip(gdf_nodos["etiqueta"], gdf_nodos["lat"]))
    for k, comp in enumerate(componentes):
        lat_media = sum(lat_de[e] for e in comp) / len(comp)
        nombre_c = f"C{k + 1} ({len(comp)} nodos, lat media {lat_media:.1f})"
        for e in comp:
            comp_de[e] = nombre_c
        if len(comp) > 2:
            btw.update(nx.betweenness_centrality(G.subgraph(comp), weight="weight", normalized=True))
    gdf_nodos["intermediacion"] = gdf_nodos["etiqueta"].map(btw).fillna(0.0)
    gdf_nodos["componente"] = gdf_nodos["etiqueta"].map(comp_de)
    gdf_nodos["rank_intermed"] = gdf_nodos.groupby("componente")["intermediacion"].rank(
        ascending=False, method="min").astype(int)
    print(f"   Grafo: {G.number_of_nodes()} nodos, {G.number_of_edges()} aristas, "
          f"{len(componentes)} componentes ({', '.join(str(len(c)) for c in componentes[:3])}, ...)")

    # --- Nodos troncales: en cada componente principal (>=100 nodos), los 5 hubs
    #     (grado >= 3) de mayor intermediación, separados al menos 100 km entre sí.
    registros = list(INVENTARIO)
    for comp in componentes:
        if len(comp) < 100:
            continue
        cand = gdf_nodos[(gdf_nodos["etiqueta"].isin(comp)) & (gdf_nodos["codigo_clase"] == "INTERCAMBIO")]
        elegidos = []
        for r in cand.sort_values("intermediacion", ascending=False).itertuples():
            if all(haversine_km(r.lon, r.lat, e.lon, e.lat) >= 100 for e in elegidos):
                elegidos.append(r)
            if len(elegidos) == 5:
                break
        for r in elegidos:
            loc, d = localidad_cercana(r.lon, r.lat)
            registros.append((f"Troncal {r.etiqueta} ({loc}, {d:.0f} km)", "Nodo troncal", "INTERCAMBIA",
                              r.lon, r.lat,
                              f"Hub grado {r.grado}; intermediación {r.intermediacion:.3f} "
                              f"(rank {r.rank_intermed} en su componente)", F_TOPO))

    # --- Ubicación en la topología: nodo de la red más cercano
    lons, lats = gdf_nodos["lon"].values, gdf_nodos["lat"].values
    filas = []
    for nombre, tipo, rol, lon, lat, detalle, fuente in registros:
        # TeleGeography publica la LOCALIDAD del aterrizaje, no el punto donde el
        # cable toca tierra: los aterrizajes se mueven a la costa real (OSM).
        lon_fuente, lat_fuente = lon, lat
        if nombre in COSTA:
            lon, lat = COSTA[nombre]
        dists = [haversine_km(lon, lat, lo, la) for lo, la in zip(lons, lats)]
        i = min(range(len(dists)), key=dists.__getitem__)
        n = gdf_nodos.iloc[i]
        filas.append({
            "nombre": nombre, "tipo": tipo, "rol": rol, "lon": lon, "lat": lat,
            "lon_fuente": lon_fuente, "lat_fuente": lat_fuente,
            "desplaz_costa_km": round(haversine_km(lon_fuente, lat_fuente, lon, lat), 2),
            "detalle": detalle, "fuente": fuente, "url_fuente": URL_FUENTE.get(nombre, ""),
            "nodo_topologia": n["etiqueta"], "dist_nodo_km": round(dists[i], 2),
            "grado_nodo": int(n["grado"]), "clase_nodo": n["codigo_clase"],
            "componente": n["componente"], "rank_intermed_nodo": int(n["rank_intermed"]),
            "geometry": Point(lon, lat), "nodo_lon": n["lon"], "nodo_lat": n["lat"],
        })

    gdf = gpd.GeoDataFrame(filas, geometry="geometry", crs="EPSG:4326")
    cols = [c for c in gdf.columns if c not in ("nodo_lon", "nodo_lat")]
    gdf[cols].drop(columns="geometry").to_csv(RUTA_CSV, index=False, encoding="utf-8-sig")
    gdf[cols].to_file(RUTA_GEOJSON, driver="GeoJSON", encoding="utf-8")

    # --- Mapa: el MISMO visualizador de mapa_infraestructura.html (main.py)
    #     con las capas y el panel de filtros de nodos clave agregados encima.
    mapa_base.generar_mapa_interactivo(gpd.read_file(RUTA_LINEAS), gpd.read_file(RUTA_NODOS), RUTA_HTML,
                                       agregar_capas_extra=lambda m: agregar_nodos_clave(m, gdf))

    pd.set_option("display.width", 220, "display.max_colwidth", 40, "display.max_rows", 200)
    print(gdf[["nombre", "rol", "nodo_topologia", "dist_nodo_km", "grado_nodo", "clase_nodo",
               "componente", "rank_intermed_nodo"]].to_string())
    print(f"{RUTA_CSV.name}, {RUTA_GEOJSON.name}, {RUTA_HTML.name}")


# -----------------------------------------------------------------------------
# CAPAS Y FILTROS DE NODOS CLAVE EN EL MAPA
# -----------------------------------------------------------------------------
COLORES_ROL = {"GENERA": "#1d4ed8", "INTERCAMBIA": "#f08c00", "RECIBE": "#2b8a3e"}
SIMBOLO_TIPO = {"Aterrizaje cable submarino": "▼", "IXP / PIT": "⇄", "Data center": "■", "Nodo troncal": "◆"}


def agregar_nodos_clave(mapa, gdf):
    """Agrega una capa por rol con los nodos clave (marcador + línea punteada al
    nodo N-xxxx asignado) y un panel de filtros por rol, tipo, componente,
    distancia y búsqueda por nombre."""
    registro = []  # metadatos que usa el JS de filtros
    for rol, color in COLORES_ROL.items():
        sub = gdf[gdf["rol"] == rol]
        fg = folium.FeatureGroup(name=f"★ Nodos clave que {rol}N ({len(sub)})", show=True)
        for r in sub.itertuples():
            linea = folium.PolyLine([(r.lat, r.lon), (r.nodo_lat, r.nodo_lon)],
                                    color=color, weight=2, dash_array="5", opacity=0.9)
            linea.add_to(fg)
            icono = folium.DivIcon(
                icon_size=(22, 22), icon_anchor=(11, 11),
                html=(f'<div style="width:22px;height:22px;line-height:20px;text-align:center;'
                      f'background:{color};color:white;border:2px solid white;border-radius:5px;'
                      f'box-shadow:0 0 3px rgba(0,0,0,.6);font-size:12px;font-weight:bold;">'
                      f'{SIMBOLO_TIPO[r.tipo]}</div>'))
            marcador = folium.Marker(
                (r.lat, r.lon), icon=icono,
                tooltip=f"<b>{r.nombre}</b> — {r.tipo} — <b>{r.rol}</b>",
                popup=folium.Popup(
                    f'<div style="font-size:12px;min-width:250px;">'
                    f'<div style="background:{color};color:white;padding:4px 8px;border-radius:4px;'
                    f'font-weight:bold;text-align:center;margin-bottom:6px;">NODO CLAVE — {r.rol}</div>'
                    f"<b>{r.nombre}</b><br>Tipo: {r.tipo}<br>{r.detalle}<br>"
                    f"Coordenadas: {r.lat:.5f}, {r.lon:.5f}"
                    + (f"<br><small style='color:#555'>Punto de llegada a la costa. La fuente da la localidad "
                       f"({r.lat_fuente:.5f}, {r.lon_fuente:.5f}), a {r.desplaz_costa_km} km.</small>"
                       if r.desplaz_costa_km > 0 else "")
                    + "<hr style='margin:4px 0'>"
                    f"Nodo de la red: <b>{r.nodo_topologia}</b> ({r.clase_nodo}, grado {r.grado_nodo})"
                    f" a {r.dist_nodo_km} km<br>Componente: {r.componente}"
                    f'<div style="margin-top:6px;padding:5px 7px;background:#f1f3f5;border-left:3px solid {color};'
                    f'border-radius:3px;"><b>Fuente:</b> {r.fuente}<br>'
                    + (f'<a href="{r.url_fuente}" target="_blank" rel="noopener">Ver registro en la fuente ↗</a>'
                       if r.url_fuente else
                       "Calculado en <code>scripts/nodos_clave.py</code> (grado ≥ 3 y mayor intermediación)")
                    + (f"<br><small>{F_COSTA}</small>" if r.tipo == "Aterrizaje cable submarino" else "")
                    + "</div></div>", max_width=330),
            )
            marcador.add_to(fg)
            registro.append({
                "m": marcador.get_name(), "l": linea.get_name(), "g": fg.get_name(),
                "nombre": r.nombre, "rol": r.rol, "tipo": r.tipo, "comp": r.componente.split(" ")[0],
                "nodo": r.nodo_topologia, "dist": float(r.dist_nodo_km), "lat": r.lat, "lon": r.lon,
            })
        fg.add_to(mapa)

    # Referencias JS: los nombres de variables de Leaflet se insertan sin comillas
    items_js = ",\n".join(
        "{m:%s,l:%s,g:%s,d:%s}" % (e["m"], e["l"], e["g"],
                                   json.dumps({k: v for k, v in e.items() if k not in ("m", "l", "g")},
                                              ensure_ascii=False))
        for e in registro)

    def chk(grupo, valor, etiqueta, color=None, cant=0):
        muestra = (f'<span style="background:{color};width:10px;height:10px;display:inline-block;'
                   f'border-radius:3px;margin-right:5px;"></span>') if color else ""
        return (f'<label style="display:flex;justify-content:space-between;align-items:center;'
                f'font-size:11.5px;margin-bottom:3px;cursor:pointer;">'
                f'<span><input type="checkbox" class="nc-{grupo}" value="{valor}" checked '
                f'onchange="ncFiltrar()" style="margin-right:5px;">{muestra}{etiqueta}</span>'
                f'<span style="font-size:10px;color:#666;background:#f1f3f5;padding:0 5px;border-radius:4px;">'
                f'{cant}</span></label>')

    roles_html = "".join(chk("rol", rol, rol, c, int((gdf["rol"] == rol).sum())) for rol, c in COLORES_ROL.items())
    tipos_html = "".join(chk("tipo", t, f"{s} {t}", None, int((gdf["tipo"] == t).sum()))
                         for t, s in SIMBOLO_TIPO.items())
    comps = sorted({e["comp"] for e in registro}, key=lambda c: int(c[1:]))
    comps_html = "".join(f'<option value="{c}">{c}</option>' for c in comps)
    dist_max = math.ceil(gdf["dist_nodo_km"].max())
    titulo_sec = 'style="font-size:10px;font-weight:700;color:#6c757d;text-transform:uppercase;margin:8px 0 4px;"'
    boton = ('style="flex:1;background:#f8f9fa;border:1px solid #ced4da;border-radius:4px;'
             'padding:3px;font-size:10.5px;cursor:pointer;"')

    panel = f"""
    <div id="nc-panel" style="position:fixed;bottom:25px;right:15px;z-index:9999;width:255px;
         background:rgba(255,255,255,.96);padding:10px 12px;border-radius:8px;border:1px solid #ced4da;
         box-shadow:0 4px 16px rgba(0,0,0,.22);font-family:-apple-system,'Segoe UI',Roboto,sans-serif;color:#212529;">
      <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #e9ecef;padding-bottom:5px;">
        <span style="font-size:12px;font-weight:700;text-transform:uppercase;">★ Filtros de nodos clave</span>
        <button id="nc-colapsar" onclick="ncColapsar()" style="background:#f8f9fa;border:1px solid #dee2e6;
                border-radius:4px;cursor:pointer;font-size:11px;padding:0 6px;font-weight:bold;">−</button>
      </div>
      <div id="nc-contenido" style="max-height:60vh;overflow-y:auto;">
        <input id="nc-buscar" type="text" placeholder="Buscar por nombre o N-xxxx…" oninput="ncFiltrar()"
               style="width:100%;box-sizing:border-box;margin-top:8px;padding:4px 6px;font-size:11.5px;
                      border:1px solid #ced4da;border-radius:4px;">
        <div style="display:flex;gap:4px;margin-top:6px;">
          <button onclick="ncTodos(true)" {boton}>✔ Todos</button>
          <button onclick="ncTodos(false)" {boton}>✖ Ninguno</button>
        </div>
        <div {titulo_sec}>Rol</div>{roles_html}
        <div {titulo_sec}>Tipo</div>{tipos_html}
        <div {titulo_sec}>Componente de la red</div>
        <select id="nc-comp" onchange="ncFiltrar()" style="width:100%;font-size:11.5px;padding:2px;">
          <option value="">Todos</option>{comps_html}
        </select>
        <div {titulo_sec}>Distancia máx. al nodo de la red: <span id="nc-dist-val">{dist_max}</span> km</div>
        <input id="nc-dist" type="range" min="0" max="{dist_max}" step="0.5" value="{dist_max}"
               oninput="ncFiltrar()" style="width:100%;">
        <label style="font-size:11.5px;display:block;margin-top:4px;cursor:pointer;">
          <input id="nc-lineas" type="checkbox" checked onchange="ncFiltrar()" style="margin-right:5px;">
          Mostrar línea al nodo asignado (N-xxxx)</label>
        <div id="nc-contador" style="font-size:11px;font-weight:600;margin-top:8px;border-top:1px solid #e9ecef;padding-top:5px;"></div>
        <div id="nc-lista" style="font-size:11px;max-height:150px;overflow-y:auto;margin-top:3px;"></div>
        <div style="font-size:10px;color:#666;margin-top:6px;">Clic en un nombre para acercar.
             Fuentes: TeleGeography, PeeringDB, topología propia.</div>
      </div>
    </div>
    <script>
    (function() {{
      let NC = null;
      function items() {{
        if (!NC) NC = [{items_js}];
        return NC;
      }}
      function marcados(clase) {{
        return Array.from(document.querySelectorAll('.nc-' + clase + ':checked')).map(x => x.value);
      }}
      window.ncFiltrar = function() {{
        const roles = marcados('rol'), tipos = marcados('tipo');
        const texto = document.getElementById('nc-buscar').value.trim().toLowerCase();
        const comp = document.getElementById('nc-comp').value;
        const dmax = parseFloat(document.getElementById('nc-dist').value);
        const conLinea = document.getElementById('nc-lineas').checked;
        document.getElementById('nc-dist-val').innerText = dmax;
        const lista = [];
        items().forEach(function(it, i) {{
          const d = it.d;
          const ok = roles.includes(d.rol) && tipos.includes(d.tipo) && (!comp || d.comp === comp)
                     && d.dist <= dmax
                     && (!texto || d.nombre.toLowerCase().includes(texto) || d.nodo.toLowerCase().includes(texto));
          if (ok) {{ if (!it.g.hasLayer(it.m)) it.g.addLayer(it.m); lista.push(i); }}
          else if (it.g.hasLayer(it.m)) it.g.removeLayer(it.m);
          if (ok && conLinea) {{ if (!it.g.hasLayer(it.l)) it.g.addLayer(it.l); }}
          else if (it.g.hasLayer(it.l)) it.g.removeLayer(it.l);
        }});
        document.getElementById('nc-contador').innerText =
          'Mostrando ' + lista.length + ' de ' + items().length + ' nodos clave';
        document.getElementById('nc-lista').innerHTML = lista.map(function(i) {{
          const d = items()[i].d;
          return '<div class="nc-item" data-i="' + i + '" style="cursor:pointer;padding:1px 0;color:#1864ab;">'
                 + d.nombre + ' <span style="color:#888">(' + d.rol + ')</span></div>';
        }}).join('');
        document.querySelectorAll('#nc-lista .nc-item').forEach(function(el) {{
          el.onclick = function() {{
            const it = items()[parseInt(el.dataset.i)];
            {mapa.get_name()}.setView([it.d.lat, it.d.lon], 11);
            it.m.openPopup();
          }};
        }});
      }};
      window.ncTodos = function(v) {{
        document.querySelectorAll('.nc-rol, .nc-tipo').forEach(x => x.checked = v);
        ncFiltrar();
      }};
      window.ncColapsar = function() {{
        const c = document.getElementById('nc-contenido'), b = document.getElementById('nc-colapsar');
        const oculto = c.style.display === 'none';
        c.style.display = oculto ? 'block' : 'none';
        b.innerText = oculto ? '−' : '+';
      }};
      window.addEventListener('load', function() {{ ncFiltrar(); }});
    }})();
    </script>"""
    mapa.get_root().html.add_child(folium.Element(panel))


if __name__ == "__main__":
    main()
