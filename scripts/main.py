"""
=============================================================================
PROYECTO: Análisis de Resiliencia de Infraestructura de Internet en Chile
SCRIPT:   main.py
DESCRIPCIÓN:
    Pipeline integral de procesamiento y análisis topológico:
      1. Lectura y unificación de 4 capas Shapefile de la red troncal.
      2. Estandarización a geometrías LineString simples (2D) para PostGIS (pgRouting).
      3. Reproyección a EPSG:4326 (WGS 84).
      4. Análisis Topológico de Grafos:
         - Extracción de coordenadas de inicio y fin de cada tramo.
         - Cálculo de grado del nodo (conteo de convergencias).
         - Clasificación funcional:
             * Nodos de Intercambio (Hubs): Grado >= 3.
             * Nodos Receptores/Generadores: Grado == 1 (estaciones de amarre / terminales).
             * Nodos Normales (Tránsito): Grado == 2 (continuidad de la línea).
      5. Exportación de datos:
         - 'infraestructura_limpia.geojson' (aristas / líneas).
         - 'nodos_clasificados.geojson' (vértices / nodos).
      6. Generación de mapa interactivo en 'mapa_infraestructura.html' (Leaflet / Folium)
         con simbología diferenciada, tooltips detallados y leyenda explicativa.
=============================================================================
"""

import os
from pathlib import Path
from collections import defaultdict
import pandas as pd
import geopandas as gpd
from pyproj import Geod
import numpy as np
from sklearn.cluster import DBSCAN
import shapely
from shapely.geometry import Point, LineString
from shapely.strtree import STRtree
import folium
from folium.plugins import Fullscreen


# =============================================================================
# 1. CONFIGURACIÓN DE RUTAS Y METADATOS
# =============================================================================
DIRECTORIO_BASE = Path(__file__).resolve().parent.parent   # raíz del proyecto
DIRECTORIO_DATOS = DIRECTORIO_BASE / "datos" / "entrada"     # shapefiles originales
DIRECTORIO_SALIDA = DIRECTORIO_BASE / "datos" / "salida"     # GeoJSON / CSV generados
DIRECTORIO_MAPAS = DIRECTORIO_BASE / "mapas"                 # visualizadores HTML

# Configuración de capas lineales
CAPAS_CONFIG = [
    {
        "archivo": "fibra_optica_detectada.shp",
        "tipo_tramo": "Fibra Óptica Existente",
        "codigo_tipo": "FIBRA",
        "color": "#1b9e77",       # Verde esmeralda (red troncal operativa)
        "peso": 3.0,
        "opacidad": 0.85,
    },
    {
        "archivo": "postacion_electrica_detectada.shp",
        "tipo_tramo": "Postación Eléctrica",
        "codigo_tipo": "POSTE",
        "color": "#d95f02",       # Naranja (postación utilizable para tendido)
        "peso": 2.5,
        "opacidad": 0.80,
    },
    {
        "archivo": "camino_sin_postacion_detectado.shp",
        "tipo_tramo": "Camino sin Postación",
        "codigo_tipo": "CAMINO",
        "color": "#7570b3",       # Violeta (requiere obra civil / zanja)
        "peso": 2.0,
        "opacidad": 0.75,
    },
    {
        "archivo": "trayecto_en_ferry.shp",
        "tipo_tramo": "Trayecto en Ferry",
        "codigo_tipo": "FERRY",
        "color": "#e7298a",       # Magenta (enlace marítimo / cruce por agua)
        "peso": 2.5,
        "opacidad": 0.90,
    },
]

# Rutas de salida
RUTA_GEOJSON_LINEAS = DIRECTORIO_SALIDA / "infraestructura_limpia.geojson"
RUTA_GEOJSON_NODOS = DIRECTORIO_SALIDA / "nodos_clasificados.geojson"
RUTA_HTML_SALIDA = DIRECTORIO_MAPAS / "mapa_infraestructura.html"


# =============================================================================
# 2. LECTURA, UNIFICACIÓN Y NORMALIZACIÓN DE ATRIBUTOS
# =============================================================================
def cargar_y_estandarizar_capa(config: dict, ruta_dir: Path) -> gpd.GeoDataFrame:
    """
    Lee un Shapefile individual y normaliza su esquema de atributos
    para evitar inconsistencias de tipos y columnas con valores nulos (NaN).
    """
    ruta_shp = ruta_dir / config["archivo"]
    if not ruta_shp.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {ruta_shp}")

    print(f"-> Leyendo: {config['archivo']}...")
    gdf = gpd.read_file(ruta_shp)
    total_filas = len(gdf)

    # Normalizar identificador original (convertir a string para evitar colisión int/str)
    if "id" in gdf.columns:
        gdf["id_origen"] = gdf["id"].fillna("SIN_ID").astype(str)
        gdf = gdf.drop(columns=["id"])
    else:
        gdf["id_origen"] = [f"orig_{i+1}" for i in range(total_filas)]

    # Metadatos canónicos
    gdf["tipo_tramo"] = config["tipo_tramo"]
    gdf["codigo_tipo"] = config["codigo_tipo"]
    gdf["capa_origen"] = config["archivo"]

    columnas_deseadas = ["geometry", "tipo_tramo", "codigo_tipo", "capa_origen", "id_origen"]
    gdf = gdf[[col for col in columnas_deseadas if col in gdf.columns]]

    print(f"   {total_filas} registros leídos.")
    return gdf


# =============================================================================
# 3. ESTANDARIZACIÓN DE GEOMETRÍAS (LINESTRINGS 2D)
# =============================================================================
def limpiar_geometrias_para_postgis(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Asegura que todas las geometrías sean LineString simples y 2D:
      - Descompone MultiLineString mediante .explode().
      - Remueve coordenadas Z/M con shapely.force_2d.
      - Asigna un identificador entero único ('id_red') como clave primaria.
    """
    print("\n[2/5] Estandarizando geometrías a LineString simples (2D)...")

    # Filtrar geometrías nulas o vacías
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()

    # Descomponer MultiLineString
    cant_multilines = (gdf.geometry.geom_type == "MultiLineString").sum()
    if cant_multilines > 0:
        print(f"   ! Separando {cant_multilines} MultiLineStrings...")
        gdf = gdf.explode(index_parts=False).reset_index(drop=True)
    else:
        print("   Sin geometrías MultiLineString detectadas.")

    # Aplanar a 2D si existen coordenadas Z
    if gdf.geometry.has_z.any():
        gdf["geometry"] = gdf.geometry.apply(shapely.force_2d)

    # Filtrar solo LineString
    gdf = gdf[gdf.geometry.geom_type == "LineString"].copy()

    # Generar ID de red único para PostGIS / pgRouting
    gdf["id_red"] = range(1, len(gdf) + 1)

    print(f"   Geometrías estandarizadas: {len(gdf)} LineStrings válidas.")
    return gdf


# =============================================================================
# 4. AJUSTE TOPOLÓGICO: SNAPPING DE EXTREMOS (< 5 METROS)
# =============================================================================
def aplicar_snapping_topologico(gdf_lineas: gpd.GeoDataFrame, tolerancia_metros: float = 5.0) -> gpd.GeoDataFrame:
    """
    Ajuste topológico de precisión (Snapping de extremos):
    Identifica extremos de líneas que se encuentran a una distancia menor a
    'tolerancia_metros' (por ejemplo, errores de digitalización / undershoots de 2 a 5 m)
    y los fusiona en su centroide común.

    Garantiza que tramos convergentes en plazas o esquinas se toquen exactamente,
    eliminando nodos espurios superpuestos y coincidiendo con la tolerancia
    utilizada por 'pgr_createTopology' en PostGIS.
    """
    print(f"\n[3/6] Aplicando snapping topológico de extremos (tolerancia: {tolerancia_metros} metros)...")

    endpoints = []
    for idx, row in gdf_lineas.iterrows():
        coords = list(row.geometry.coords)
        endpoints.append((idx, 0, coords[0]))
        endpoints.append((idx, -1, coords[-1]))

    pts_arr = np.array([ep[2] for ep in endpoints])  # [lon, lat]

    # DBSCAN con métrica haversine sobre la esfera WGS84
    kms_per_radian = 6371.0088
    eps_rad = (tolerancia_metros / 1000.0) / kms_per_radian
    rad_coords = np.radians(np.array([[p[1], p[0]] for p in pts_arr]))  # [lat, lon]

    db = DBSCAN(eps=eps_rad, min_samples=1, metric="haversine").fit(rad_coords)
    labels = db.labels_

    # Calcular centroide para cada cluster
    centros = {}
    clusters_fusionados = 0
    for cid in set(labels):
        mask = (labels == cid)
        pts = pts_arr[mask]
        if len(pts) > 1 and (np.ptp(pts[:, 0]) > 1e-7 or np.ptp(pts[:, 1]) > 1e-7):
            clusters_fusionados += 1
        centro = pts.mean(axis=0)
        centros[cid] = (round(centro[0], 6), round(centro[1], 6))

    # Actualizar geometrías de las líneas con los extremos ajustados
    nuevas_geometrias = []
    for idx, row in gdf_lineas.iterrows():
        c = list(row.geometry.coords)
        s = centros[labels[2 * idx]]
        e = centros[labels[2 * idx + 1]]
        # Protección contra líneas ultracortas (< tolerancia) para no colapsarlas a un punto
        if s == e:
            s = c[0]
            e = c[-1]
        
        if len(c) == 2:
            nuevas_geometrias.append(LineString([s, e]))
        else:
            nuevas_geometrias.append(LineString([s] + c[1:-1] + [e]))

    gdf_snapped = gdf_lineas.copy()
    gdf_snapped["geometry"] = nuevas_geometrias
    print(f"   Snapping completado: {clusters_fusionados} micro-gaps corregidos (< {tolerancia_metros} m).")
    return gdf_snapped


# =============================================================================
# 5. CÁLCULO DE LONGITUDES GEODÉSICAS (WGS 84)
# =============================================================================
def calcular_longitudes_geodesicas(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Calcula la longitud real en kilómetros sobre el elipsoide WGS84 con PyProj.
    """
    print("\n[3/5] Calculando longitudes geodésicas (WGS84)...")
    geod = Geod(ellps="WGS84")

    gdf["longitud_km"] = gdf.geometry.apply(
        lambda g: round(geod.geometry_length(g) / 1000.0, 3)
    )

    resumen = gdf.groupby("tipo_tramo")["longitud_km"].agg(["count", "sum"]).reset_index()
    resumen.columns = ["Tipo de Tramo", "Cantidad", "Longitud Total (km)"]
    print("\n" + "-" * 60)
    print("ESTADÍSTICAS DE LA RED:")
    print("-" * 60)
    for _, fila in resumen.iterrows():
        print(f" • {fila['Tipo de Tramo']:<26}: {int(fila['Cantidad']):>5} tramos | {fila['Longitud Total (km)']:>9.2f} km")
    print("-" * 60)
    print(f" TOTAL RED UNIFICADA       : {len(gdf):>5} tramos | {gdf['longitud_km'].sum():>9.2f} km\n")

    return gdf


# =============================================================================
# 5. EXTRACCIÓN Y CLASIFICACIÓN DE NODOS (TOPOLOGÍA DE GRAFOS)
# =============================================================================
def extraer_y_clasificar_nodos(gdf_lineas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Analiza rigurosamente la topología de la red mediante indexación espacial:

    POR QUÉ EL CONTEO SIMPLE DE EXTREMOS FALLA EN SIG:
      - Si una derivación o cable secundario termina en un punto P que forma
        una "T" con un cable troncal principal continuo, P solo es extremo del
        cable secundario.
      - Para el cable troncal, P es un vértice o punto interior intermedio.
      - Un conteo ingenuo de extremos contaba P como '1', clasificándolo
        erróneamente como 'Receptor/Generador', a pesar de que visual y físicamente
        convergen 3 ramas de cable (1 ramal entrante + 2 direcciones de la troncal pasante).
    
    SOLUCIÓN IMPLEMENTADA (GRADO TOPOLÓGICO REAL):
      1. Extrae los extremos de cada línea.
      2. Utiliza un índice espacial (STRtree) para detectar si el punto es
         atravesado por alguna otra línea troncal (unión en T / empalme).
      3. Grado Real = (Cables que terminan) + 2 * (Líneas pasantes).
      4. Clasificación:
         - Grado 1 : Verdadero Receptor / Generador (Extremo real, amarre submarino, terminal aislado).
         - Grado 2 : Nodo Normal (Continuidad lineal o empalme de 2 cables).
         - Grado >= 3: Nodo de Intercambio (Hub / Bifurcación / Cruce en T).
    """
    print("[4/5] Analizando topología espacial rigurosa de nodos (STRtree)...")

    # Paso 1: Mapear extremos de cada línea
    coords_info = defaultdict(lambda: {
        "terminan": [],      # IDs de tramos que inician o terminan en este punto
        "atraviesan": [],    # IDs de tramos troncales continuos que pasan por este punto
        "tipos_cable": set(),
        "detalles_terminan": [],
        "detalles_atraviesan": []
    })

    for _, fila in gdf_lineas.iterrows():
        coords = list(fila.geometry.coords)
        if len(coords) < 2:
            continue
        
        p_inicio = (round(coords[0][0], 6), round(coords[0][1], 6))
        p_fin = (round(coords[-1][0], 6), round(coords[-1][1], 6))
        
        id_tramo = fila["id_red"]
        tipo = fila["tipo_tramo"]

        for p in (p_inicio, p_fin):
            coords_info[p]["terminan"].append(id_tramo)
            coords_info[p]["detalles_terminan"].append(f"#{id_tramo} ({tipo})")
            coords_info[p]["tipos_cable"].add(tipo)

    # Paso 2: Detectar líneas pasantes (uniones en T) usando STRtree
    print("   -> Verificando intersecciones con líneas pasantes (empalmes en T)...")
    arbol_espacial = STRtree(gdf_lineas.geometry.values)

    for coord, data in coords_info.items():
        pt = Point(coord)
        # Buffer de búsqueda micro-espacial (~0.5 metros)
        candidatos_idx = arbol_espacial.query(pt.buffer(0.000005))
        ids_terminan = set(data["terminan"])

        for c_idx in candidatos_idx:
            linea = gdf_lineas.iloc[c_idx]
            id_linea = linea["id_red"]

            # Si la línea NO tiene este punto como extremo, verificar si pasa por él
            if id_linea not in ids_terminan:
                if pt.distance(linea.geometry) < 0.000002:  # ~0.2 metros
                    data["atraviesan"].append(id_linea)
                    data["detalles_atraviesan"].append(f"#{id_linea} ({linea['tipo_tramo']})")
                    data["tipos_cable"].add(linea["tipo_tramo"])

    # Paso 3: Calcular Grado Real y Clasificar
    registros_nodos = []
    for idx, (coord, info) in enumerate(coords_info.items(), start=1):
        lon, lat = coord
        cables_terminan = len(info["terminan"])
        cables_atraviesan = len(info["atraviesan"])

        # Grado físico real: cada línea pasante aporta 2 ramas de conectividad
        grado_real = cables_terminan + (2 * cables_atraviesan)

        if grado_real >= 3:
            clase = "Nodo de Intercambio (Hub)"
            cod_clase = "INTERCAMBIO"
            if cables_atraviesan > 0:
                desc = f"Empalme en 'T' / Bifurcación ({cables_terminan} ramal + troncal pasante #{info['atraviesan'][0]})"
            else:
                desc = f"Punto de convergencia múltiple ({grado_real} cables terminando aquí)"
        elif grado_real == 1:
            clase = "Nodo Receptor / Generador"
            cod_clase = "RECEPTOR_GENERADOR"
            desc = "Extremo terminal 100% aislado (amarre submarino / fin de red)"
        else:
            clase = "Nodo Normal (Tránsito)"
            cod_clase = "TRANSITO"
            desc = "Continuidad lineal (2 cables empalmados)"

        registros_nodos.append({
            "id_nodo": idx,
            "etiqueta": f"N-{idx:04d}",
            "clasificacion": clase,
            "codigo_clase": cod_clase,
            "grado": grado_real,
            "cables_terminan": cables_terminan,
            "cables_pasan": cables_atraviesan,
            "descripcion": desc,
            "tipos_conectados": ", ".join(sorted(info["tipos_cable"])),
            "cant_tipos": len(info["tipos_cable"]),
            "tramos_terminan": ", ".join(info["detalles_terminan"][:10]),
            "tramos_pasan": ", ".join(info["detalles_atraviesan"][:5]) if info["atraviesan"] else "Ninguno (no es unión en T)",
            "lon": lon,
            "lat": lat,
            "geometry": Point(lon, lat)
        })

    gdf_nodos = gpd.GeoDataFrame(registros_nodos, geometry="geometry", crs="EPSG:4326")

    # Resumen de clasificación
    conteo_clases = gdf_nodos["clasificacion"].value_counts()
    print("\n" + "-" * 60)
    print("CLASIFICACIÓN TOPOLÓGICA REAL (CORREGIDA):")
    print("-" * 60)
    for nombre_clase, cant in conteo_clases.items():
        porc = (cant / len(gdf_nodos)) * 100
        print(f" • {nombre_clase:<30}: {cant:>5} nodos ({porc:>5.1f}%)")
    print("-" * 60)
    print(f" TOTAL NODOS ÚNICOS IDENTIFICADOS : {len(gdf_nodos):>5} nodos\n")

    return gdf_nodos


# =============================================================================
# 6. EXPORTACIONES GEOJSON (TRAMOS Y NODOS)
# =============================================================================
def exportar_capas_geojson(gdf_lineas: gpd.GeoDataFrame, gdf_nodos: gpd.GeoDataFrame):
    """
    Exporta las capas de líneas y nodos en formato GeoJSON optimizado.
    Maneja el posible bloqueo de archivos en Windows si están abiertos en otra app.
    """
    print("-> Exportando GeoJSONs estandarizados...")

    # 1. GeoJSON de Líneas
    try:
        gdf_lineas_exp = gdf_lineas.copy()
        gdf_lineas_exp["geometry"] = shapely.set_precision(gdf_lineas_exp["geometry"], grid_size=0.000001)
        gdf_lineas_exp.to_file(RUTA_GEOJSON_LINEAS, driver="GeoJSON", encoding="utf-8")
        mb_l = RUTA_GEOJSON_LINEAS.stat().st_size / (1024 * 1024)
        print(f"   GeoJSON Líneas: {RUTA_GEOJSON_LINEAS.name} ({mb_l:.2f} MB)")
    except PermissionError:
        ruta_alt = RUTA_GEOJSON_LINEAS.parent / f"{RUTA_GEOJSON_LINEAS.stem}_nuevo.geojson"
        gdf_lineas.to_file(ruta_alt, driver="GeoJSON", encoding="utf-8")
        print(f"   ! Líneas guardadas como: {ruta_alt.name} (archivo original bloqueado)")

    # 2. GeoJSON de Nodos
    try:
        gdf_nodos_exp = gdf_nodos.copy()
        gdf_nodos_exp["geometry"] = shapely.set_precision(gdf_nodos_exp["geometry"], grid_size=0.000001)
        gdf_nodos_exp.to_file(RUTA_GEOJSON_NODOS, driver="GeoJSON", encoding="utf-8")
        mb_n = RUTA_GEOJSON_NODOS.stat().st_size / (1024 * 1024)
        print(f"   GeoJSON Nodos : {RUTA_GEOJSON_NODOS.name} ({mb_n:.2f} MB)")
    except PermissionError:
        ruta_alt = RUTA_GEOJSON_NODOS.parent / f"{RUTA_GEOJSON_NODOS.stem}_nuevo.geojson"
        gdf_nodos.to_file(ruta_alt, driver="GeoJSON", encoding="utf-8")
        print(f"   ! Nodos guardados como: {ruta_alt.name} (archivo original bloqueado)")


# =============================================================================
# 7. GENERACIÓN DEL MAPA WEB INTERACTIVO (LEAFLET / FOLIUM)
# =============================================================================
def generar_mapa_interactivo(gdf_lineas: gpd.GeoDataFrame, gdf_nodos: gpd.GeoDataFrame, ruta_html: Path,
                             agregar_capas_extra=None):
    """
    Construye el visualizador interactivo con Folium incorporando:
      - Tramos de infraestructura organizados por tipo con simbología y tooltip.
      - Nodos topológicos diferenciados por grado:
          * Nodos de Intercambio (Rojo Carmesí, radio proporcional al grado).
          * Nodos Receptores/Generadores (Azul Cian Eléctrico, radio fijo destacado).
          * Nodos Normales (Gris neutro, desactivados por defecto en LayerControl para agilidad).
      - Popups y tooltips detallados.
      - Leyenda flotante con documentación técnica completa.
    """
    print(f"-> Generando mapa web con visualización de nodos en: {ruta_html.name}...")

    minx, miny, maxx, maxy = gdf_lineas.total_bounds
    centro_lat = (miny + maxy) / 2.0
    centro_lon = (minx + maxx) / 2.0

    # Inicializar mapa con Leaflet Canvas habilitado
    mapa = folium.Map(
        location=[centro_lat, centro_lon],
        zoom_start=5,
        tiles=None,
        prefer_canvas=True
    )

    # -------------------------------------------------------------------------
    # CAPAS BASE
    # -------------------------------------------------------------------------
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="&copy; OpenStreetMap contributors &copy; CARTO",
        name="CartoDB Positron (Claro - Recomendado)",
        control=True
    ).add_to(mapa)

    folium.TileLayer(
        tiles="OpenStreetMap",
        name="OpenStreetMap (Estándar)",
        control=True
    ).add_to(mapa)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri, Maxar, Earthstar Geographics",
        name="Esri Satélite (Imagen Aérea)",
        control=True
    ).add_to(mapa)

    # -------------------------------------------------------------------------
    # CAPAS VECTORIALES DE TRAMOS (LÍNEAS)
    # -------------------------------------------------------------------------
    estilos_lineas = {c["tipo_tramo"]: c for c in CAPAS_CONFIG}
    grupos_lineas_info = []

    for tipo, conf in estilos_lineas.items():
        sub_gdf = gdf_lineas[gdf_lineas["tipo_tramo"] == tipo]
        if sub_gdf.empty:
            continue

        grupo_linea = folium.FeatureGroup(name=f"Línea: {tipo} ({len(sub_gdf)} tramos)", show=True)

        color = conf["color"]
        peso = conf["peso"]
        opacidad = conf["opacidad"]

        def style_fn(feat, c=color, w=peso, o=opacidad):
            return {"color": c, "weight": w, "opacity": o}

        def highlight_fn(feat):
            return {"color": "#ffff00", "weight": 5.0, "opacity": 1.0}

        folium.GeoJson(
            sub_gdf,
            name=tipo,
            style_function=style_fn,
            highlight_function=highlight_fn,
            tooltip=folium.GeoJsonTooltip(
                fields=["id_red", "tipo_tramo", "longitud_km", "id_origen"],
                aliases=["ID Tramo:", "Infraestructura:", "Longitud (km):", "ID Origen:"],
                localize=True
            ),
            popup=folium.GeoJsonPopup(
                fields=["id_red", "tipo_tramo", "longitud_km", "capa_origen", "id_origen"],
                aliases=["ID de Tramo:", "Tipo de Infraestructura:", "Longitud Real:", "Capa Fuente:", "ID Original:"],
                localize=True
            )
        ).add_to(grupo_linea)

        grupo_linea.add_to(mapa)
        grupos_lineas_info.append({
            "var_name": grupo_linea.get_name(),
            "nombre": tipo,
            "color": conf["color"],
            "cant": len(sub_gdf),
            "codigo": conf["codigo_tipo"]
        })

    # -------------------------------------------------------------------------
    # CAPAS DE NODOS TOPOLÓGICOS (PUNTOS)
    # -------------------------------------------------------------------------
    fg_intercambio = folium.FeatureGroup(
        name="Hubs de Intercambio (Grado ≥ 3) [Rojo]",
        show=True
    )
    fg_receptores = folium.FeatureGroup(
        name="Receptores / Generadores (Grado 1) [Cian]",
        show=True
    )
    # ACTIVADO POR DEFECTO para que los nodos de tránsito sean visibles de inmediato
    fg_normales = folium.FeatureGroup(
        name="Nodos Normales (Grado 2 - Tránsito) [Gris]",
        show=True
    )

    for _, nodo in gdf_nodos.iterrows():
        lat = nodo["lat"]
        lon = nodo["lon"]
        grado = nodo["grado"]
        clase = nodo["codigo_clase"]
        etiqueta = nodo["etiqueta"]
        tipos = nodo["tipos_conectados"]
        cables_term = nodo["cables_terminan"]
        cables_pas = nodo["cables_pasan"]

        if clase == "INTERCAMBIO":
            # Nodos de intercambio: radio dinámico según grado (más convergencia = círculo más grande)
            radio = min(5.0 + grado * 1.5, 14.0)
            color_borde = "#800020"    # Borgoña oscuro
            color_relleno = "#e63946"  # Rojo vivo
            opacidad = 0.90
            grupo_destino = fg_intercambio
            badge_color = "#e63946"
            badge_texto = f"HUB DE INTERCAMBIO ({grado} RAMAS / CABLES)"

        elif clase == "RECEPTOR_GENERADOR":
            # Nodos terminales genuinos / amarres submarinos
            radio = 5.5
            color_borde = "#005f73"    # Azul petróleo
            color_relleno = "#00b4d8"  # Azul cian eléctrico
            opacidad = 0.95
            grupo_destino = fg_receptores
            badge_color = "#00b4d8"
            badge_texto = "RECEPTOR / GENERADOR (EXTREMO ÚNICO)"

        else:
            # Nodos normales (tránsito lineal con alto contraste visual)
            radio = 3.5
            color_borde = "#212529"    # Borde negro carbón nítido
            color_relleno = "#5c677d"  # Gris pizarra visible
            opacidad = 0.85
            grupo_destino = fg_normales
            badge_color = "#5c677d"
            badge_texto = "NODO NORMAL (TRÁNSITO 2 CABLES)"

        # Tooltip rápido al posar el cursor
        tooltip_html = (
            f"<b>{etiqueta}</b> | <b>{nodo['clasificacion']}</b><br>"
            f"Conectividad: <b>{grado} cables</b> ({tipos})<br>"
            f"<small>Terminan: {cables_term} | Atraviesan (T): {cables_pas}</small>"
        )

        # Popup interactivo al hacer clic
        popup_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; min-width: 250px;">
            <div style="background-color: {badge_color}; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; margin-bottom: 8px; text-align: center;">
                {badge_texto}
            </div>
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="color:#666; padding: 2px 0;">ID Nodo:</td><td><b>{etiqueta}</b></td></tr>
                <tr><td style="color:#666; padding: 2px 0;">Grado Total:</td><td><b>{grado} cables / ramas</b></td></tr>
                <tr><td style="color:#666; padding: 2px 0;">Cables que terminan:</td><td><b>{cables_term}</b> ({nodo['tramos_terminan']})</td></tr>
                <tr><td style="color:#666; padding: 2px 0;">Troncal que pasa (T):</td><td><b>{cables_pas}</b> ({nodo['tramos_pasan']})</td></tr>
                <tr><td style="color:#666; padding: 2px 0;">Tecnología:</td><td>{tipos}</td></tr>
                <tr><td style="color:#666; padding: 2px 0;">Coordenadas:</td><td><small>{lat:.5f}, {lon:.5f}</small></td></tr>
            </table>
            <div style="font-size: 11px; color: #444; margin-top: 6px; padding-top: 4px; border-top: 1px solid #ddd; font-style: italic;">
                {nodo['descripcion']}
            </div>
        </div>
        """

        folium.CircleMarker(
            location=[lat, lon],
            radius=radio,
            color=color_borde,
            weight=1.2,
            fill=True,
            fill_color=color_relleno,
            fill_opacity=opacidad,
            tooltip=tooltip_html,
            popup=folium.Popup(popup_html, max_width=300)
        ).add_to(grupo_destino)

    fg_intercambio.add_to(mapa)
    fg_receptores.add_to(mapa)
    fg_normales.add_to(mapa)

    # Capas adicionales opcionales (ej. nodos_clave.py agrega los nodos clave)
    if agregar_capas_extra is not None:
        agregar_capas_extra(mapa)

    # -------------------------------------------------------------------------
    # AJUSTES DE ENCUADRE Y CONTROLES
    # -------------------------------------------------------------------------
    mapa.fit_bounds([[miny, minx], [maxy, maxx]])
    folium.LayerControl(collapsed=False, position="topright").add_to(mapa)
    Fullscreen(position="topleft").add_to(mapa)

    # -------------------------------------------------------------------------
    # LEYENDA DOCUMENTADA FLOTANTE (HTML/CSS)
    # -------------------------------------------------------------------------
    n_hubs = (gdf_nodos["codigo_clase"] == "INTERCAMBIO").sum()
    n_rec = (gdf_nodos["codigo_clase"] == "RECEPTOR_GENERADOR").sum()
    n_norm = (gdf_nodos["codigo_clase"] == "TRANSITO").sum()

    lineas_leyenda = "".join([
        f'<div style="margin-bottom: 4px;">'
        f'<span style="background:{c["color"]}; width:20px; height:4px; display:inline-block; vertical-align:middle; margin-right:8px; border-radius:2px;"></span>'
        f'<span style="font-size: 12px; color: #333;">{c["tipo_tramo"]}</span>'
        f'</div>'
        for c in CAPAS_CONFIG
    ])

    html_leyenda = f"""
    <div style="
        position: fixed;
        bottom: 25px;
        left: 25px;
        z-index: 9999;
        background-color: rgba(255, 255, 255, 0.95);
        padding: 14px 18px;
        border-radius: 8px;
        box-shadow: 0 3px 14px rgba(0,0,0,0.25);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        color: #222;
        min-width: 250px;
        max-width: 320px;
        border: 1px solid #ccc;
    ">
        <!-- TÍTULO -->
        <h4 style="margin: 0 0 8px 0; font-size: 13px; font-weight: 700; border-bottom: 2px solid #ddd; padding-bottom: 4px; text-transform: uppercase; color: #111;">
            Red Troncal de Internet (Chile)
        </h4>

        <!-- SECCIÓN TRAMOS -->
        <div style="font-size: 11px; font-weight: 700; color: #666; margin: 6px 0 4px 0; text-transform: uppercase;">
            Tramos de Conectividad (Líneas)
        </div>
        {lineas_leyenda}

        <!-- SECCIÓN NODOS TOPOLÓGICOS -->
        <div style="font-size: 11px; font-weight: 700; color: #666; margin: 10px 0 6px 0; text-transform: uppercase; border-top: 1px solid #eee; padding-top: 6px;">
            Nodos Topológicos de la Red (por grado)
        </div>

        <!-- 1. NODOS DE INTERCAMBIO -->
        <div style="margin-bottom: 6px;">
            <span style="background: #e63946; border: 1.5px solid #800020; width: 12px; height: 12px; display: inline-block; border-radius: 50%; vertical-align: middle; margin-right: 6px;"></span>
            <span style="font-size: 12px; font-weight: 600; color: #111;">Nodo de Intercambio (Hub)</span>
            <div style="font-size: 10px; color: #555; margin-left: 20px;">
                <b>≥ 3 cables</b> ({n_hubs} nodos). Cruces múltiples y empalmes en "T" con líneas troncales pasantes.
            </div>
        </div>

        <!-- 2. NODOS RECEPTORES / GENERADORES -->
        <div style="margin-bottom: 6px;">
            <span style="background: #00b4d8; border: 1.5px solid #005f73; width: 10px; height: 10px; display: inline-block; border-radius: 50%; vertical-align: middle; margin-right: 6px;"></span>
            <span style="font-size: 12px; font-weight: 600; color: #111;">Receptor / Generador</span>
            <div style="font-size: 10px; color: #555; margin-left: 20px;">
                <b>1 cable único (extremo terminal)</b> ({n_rec} nodos). Extremos de la red (puntas de cable).
            </div>
        </div>

        <!-- 3. NODOS NORMALES -->
        <div style="margin-bottom: 4px;">
            <span style="background: #adb5bd; border: 1px solid #495057; width: 8px; height: 8px; display: inline-block; border-radius: 50%; vertical-align: middle; margin-right: 6px;"></span>
            <span style="font-size: 12px; color: #666;">Nodo Normal / Tránsito</span>
            <div style="font-size: 10px; color: #777; margin-left: 20px;">
                <b>2 cables</b> ({n_norm} nodos). Empalmes simples y continuidad lineal.
            </div>
        </div>

        <!-- PIE DE LEYENDA -->
        <div style="font-size: 10px; color: #777; margin-top: 8px; border-top: 1px solid #eee; padding-top: 4px;">
            Red: <b>{len(gdf_lineas):,}</b> tramos ({gdf_lineas['longitud_km'].sum():,.1f} km) | <b>{len(gdf_nodos):,}</b> nodos
        </div>
    </div>
    """
    mapa.get_root().html.add_child(folium.Element(html_leyenda))

    # -------------------------------------------------------------------------
    # PANEL DE CONTROL Y FILTROS INTERACTIVOS (HTML/CSS/JS)
    # -------------------------------------------------------------------------
    # Mapeo de referencias JS de capas para manipulación en tiempo real
    lineas_chk_items = "".join([
        f"""
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px;">
            <label style="cursor: pointer; display: flex; align-items: center; font-size: 11.5px; color: #333;">
                <input type="checkbox" id="chk_linea_{item['codigo']}" checked
                       style="margin-right: 6px; cursor: pointer; accent-color: {item['color']};"
                       onchange="toggleCapaDirecta('{item['codigo']}', this.checked)">
                <span style="background:{item['color']}; width:14px; height:3px; display:inline-block; margin-right:6px; border-radius:1px;"></span>
                {item['nombre']}
            </label>
            <span style="font-size: 10px; color: #888; background: #f1f3f5; padding: 1px 5px; border-radius: 4px;">{item['cant']}</span>
        </div>
        """
        for item in grupos_lineas_info
    ])

    capas_js_dict_str = ", ".join([f"'{item['codigo']}': {item['var_name']}" for item in grupos_lineas_info])

    html_panel_filtros = f"""
    <div id="panel-filtros-red" style="
        position: fixed;
        top: 75px;
        left: 15px;
        z-index: 9999;
        background-color: rgba(255, 255, 255, 0.96);
        padding: 12px 14px;
        border-radius: 8px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.22);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        color: #212529;
        width: 255px;
        border: 1px solid #ced4da;
        backdrop-filter: blur(4px);
    ">
        <!-- CABECERA DEL PANEL -->
        <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #e9ecef; padding-bottom: 6px; margin-bottom: 8px;">
            <div style="display: flex; align-items: center;">
                <span style="font-size: 14px; margin-right: 6px;"></span>
                <span style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #111;">Filtros de Red</span>
            </div>
            <button id="btn-colapsar-filtro" onclick="togglePanelFiltros()"
                    style="background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; cursor: pointer; font-size: 11px; padding: 1px 6px; font-weight: bold; color: #495057;">
                −
            </button>
        </div>

        <div id="panel-filtro-contenido">
            <!-- BOTONES DE ACCIÓN RÁPIDA -->
            <div style="font-size: 10px; font-weight: 700; color: #6c757d; text-transform: uppercase; margin-bottom: 4px;">Modo de Vista Nodos</div>
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 4px; margin-bottom: 10px;">
                <button onclick="filtrarModoNodos('todos')"
                        style="background: #e7f5ff; border: 1px solid #74c0fc; color: #1864ab; border-radius: 4px; padding: 4px 2px; font-size: 10px; font-weight: 600; cursor: pointer;"
                        title="Activa todos los nodos incluyendo los de tránsito">
                    Todos
                </button>
                <button onclick="filtrarModoNodos('claves')"
                        style="background: #fff3bf; border: 1px solid #ffd43b; color: #f08c00; border-radius: 4px; padding: 4px 2px; font-size: 10px; font-weight: 600; cursor: pointer;"
                        title="Muestra solo Hubs y Receptores, ocultando los de tránsito">
                    ⭐ Claves
                </button>
                <button onclick="filtrarModoNodos('ninguno')"
                        style="background: #f1f3f5; border: 1px solid #ced4da; color: #495057; border-radius: 4px; padding: 4px 2px; font-size: 10px; font-weight: 600; cursor: pointer;"
                        title="Oculta todos los nodos para ver solo el trazado de cables">
                    Ocultar
                </button>
            </div>

            <!-- SECCIÓN: CAPAS DE NODOS -->
            <div style="font-size: 10px; font-weight: 700; color: #6c757d; text-transform: uppercase; margin-bottom: 6px; border-top: 1px solid #e9ecef; padding-top: 6px;">
                Nodos Topológicos
            </div>

            <!-- CHECKBOX HUBS -->
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px;">
                <label style="cursor: pointer; display: flex; align-items: center; font-size: 11.5px; color: #111;">
                    <input type="checkbox" id="chk_nodos_hubs" checked
                           style="margin-right: 6px; cursor: pointer; accent-color: #e63946;"
                           onchange="toggleCapaDirecta('nodos_hubs', this.checked)">
                    <span style="background: #e63946; border: 1px solid #800020; width: 10px; height: 10px; display: inline-block; border-radius: 50%; margin-right: 6px;"></span>
                    Hubs (≥3 cables)
                </label>
                <span style="font-size: 10px; font-weight: 600; color: #e63946; background: #ffe3e3; padding: 1px 5px; border-radius: 4px;">{n_hubs}</span>
            </div>

            <!-- CHECKBOX RECEPTORES -->
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px;">
                <label style="cursor: pointer; display: flex; align-items: center; font-size: 11.5px; color: #111;">
                    <input type="checkbox" id="chk_nodos_rec" checked
                           style="margin-right: 6px; cursor: pointer; accent-color: #00b4d8;"
                           onchange="toggleCapaDirecta('nodos_rec', this.checked)">
                    <span style="background: #00b4d8; border: 1px solid #005f73; width: 9px; height: 9px; display: inline-block; border-radius: 50%; margin-right: 6px;"></span>
                    Receptores (1 cable)
                </label>
                <span style="font-size: 10px; font-weight: 600; color: #0b7285; background: #e3fafc; padding: 1px 5px; border-radius: 4px;">{n_rec}</span>
            </div>

            <!-- CHECKBOX TRÁNSITO (EL NODO QUE EL USUARIO BUSCABA) -->
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                <label style="cursor: pointer; display: flex; align-items: center; font-size: 11.5px; color: #111; font-weight: 500;">
                    <input type="checkbox" id="chk_nodos_tra" checked
                           style="margin-right: 6px; cursor: pointer; accent-color: #5c677d;"
                           onchange="toggleCapaDirecta('nodos_tra', this.checked)">
                    <span style="background: #5c677d; border: 1px solid #212529; width: 8px; height: 8px; display: inline-block; border-radius: 50%; margin-right: 6px;"></span>
                    Nodos de Tránsito
                </label>
                <span style="font-size: 10px; font-weight: 600; color: #495057; background: #e9ecef; padding: 1px 5px; border-radius: 4px;">{n_norm}</span>
            </div>

            <!-- SECCIÓN: CAPAS DE LÍNEAS -->
            <div style="font-size: 10px; font-weight: 700; color: #6c757d; text-transform: uppercase; margin-bottom: 6px; border-top: 1px solid #e9ecef; padding-top: 6px;">
                Tramos de Infraestructura
            </div>
            {lineas_chk_items}
        </div>
    </div>

    <!-- SCRIPT DE CONTROL DINÁMICO DE CAPAS LEAFLET -->
    <script>
    (function() {{
        let mapaInstancia = null;

        function obtenerMapa() {{
            if (mapaInstancia) return mapaInstancia;
            if (typeof {mapa.get_name()} !== 'undefined') {{
                mapaInstancia = {mapa.get_name()};
                return mapaInstancia;
            }}
            return null;
        }}

        window.capasReferencia = {{
            'nodos_hubs': {fg_intercambio.get_name()},
            'nodos_rec': {fg_receptores.get_name()},
            'nodos_tra': {fg_normales.get_name()},
            {capas_js_dict_str}
        }};

        window.toggleCapaDirecta = function(clave, activo) {{
            const map = obtenerMapa();
            if (!map) return;
            const capa = window.capasReferencia[clave];
            if (!capa) return;
            if (activo) {{
                if (!map.hasLayer(capa)) map.addLayer(capa);
            }} else {{
                if (map.hasLayer(capa)) map.removeLayer(capa);
            }}
        }};

        window.filtrarModoNodos = function(modo) {{
            const chkHubs = document.getElementById('chk_nodos_hubs');
            const chkRec = document.getElementById('chk_nodos_rec');
            const chkTra = document.getElementById('chk_nodos_tra');

            if (modo === 'todos') {{
                chkHubs.checked = true;
                chkRec.checked = true;
                chkTra.checked = true;
                window.toggleCapaDirecta('nodos_hubs', true);
                window.toggleCapaDirecta('nodos_rec', true);
                window.toggleCapaDirecta('nodos_tra', true);
            }} else if (modo === 'claves') {{
                chkHubs.checked = true;
                chkRec.checked = true;
                chkTra.checked = false;
                window.toggleCapaDirecta('nodos_hubs', true);
                window.toggleCapaDirecta('nodos_rec', true);
                window.toggleCapaDirecta('nodos_tra', false);
            }} else if (modo === 'ninguno') {{
                chkHubs.checked = false;
                chkRec.checked = false;
                chkTra.checked = false;
                window.toggleCapaDirecta('nodos_hubs', false);
                window.toggleCapaDirecta('nodos_rec', false);
                window.toggleCapaDirecta('nodos_tra', false);
            }}
        }};

        window.togglePanelFiltros = function() {{
            const cont = document.getElementById('panel-filtro-contenido');
            const btn = document.getElementById('btn-colapsar-filtro');
            if (cont.style.display === 'none') {{
                cont.style.display = 'block';
                btn.innerText = '−';
            }} else {{
                cont.style.display = 'none';
                btn.innerText = '+';
            }}
        }};
    }})();
    </script>
    """
    mapa.get_root().html.add_child(folium.Element(html_panel_filtros))

    # Guardar con tolerancia a bloqueo de archivo en Windows
    try:
        mapa.save(str(ruta_html))
        print(f"   Mapa interactivo guardado: {ruta_html.resolve()}")
    except PermissionError:
        ruta_alt = ruta_html.parent / f"{ruta_html.stem}_nuevo.html"
        print(f"\n   [!] AVISO: '{ruta_html.name}' está abierto en el navegador.")
        print(f"       Guardando automáticamente como: '{ruta_alt.name}'...")
        mapa.save(str(ruta_alt))
        print(f"   Guardado como: {ruta_alt.resolve()}")


# =============================================================================
# 8. EJECUCIÓN PRINCIPAL
# =============================================================================
def main():
    print("=" * 70)
    print("PROCESAMIENTO Y ANÁLISIS TOPOLÓGICO DE LA RED TRONCAL GIS (CHILE)")
    print("=" * 70)

    # 1. Lectura y unificación
    print("\n[1/5] Leyendo capas Shapefile...")
    gdfs = [cargar_y_estandarizar_capa(c, DIRECTORIO_DATOS) for c in CAPAS_CONFIG]
    df_unificado = pd.concat(gdfs, ignore_index=True)
    gdf_unificado = gpd.GeoDataFrame(df_unificado, geometry="geometry", crs=gdfs[0].crs)
    print(f"   Capas combinadas: {len(gdf_unificado)} registros.")

    # 2. Estandarización de geometrías
    gdf_lineas = limpiar_geometrias_para_postgis(gdf_unificado)

    # 3. Verificación de proyección a EPSG:4326
    if gdf_lineas.crs is None:
        gdf_lineas.set_crs(epsg=4326, inplace=True)
    elif gdf_lineas.crs.to_epsg() != 4326:
        gdf_lineas = gdf_lineas.to_crs(epsg=4326)

    # 4. Ajuste Topológico (Snapping de extremos < 5 metros)
    gdf_lineas = aplicar_snapping_topologico(gdf_lineas, tolerancia_metros=5.0)

    # 5. Métricas de longitud
    gdf_lineas = calcular_longitudes_geodesicas(gdf_lineas)

    # 6. Extracción y Clasificación Topológica de Nodos
    gdf_nodos = extraer_y_clasificar_nodos(gdf_lineas)

    # 7. Exportaciones
    print("[6/6] Exportando entregables de red y mapa interactivo...")
    exportar_capas_geojson(gdf_lineas, gdf_nodos)
    generar_mapa_interactivo(gdf_lineas, gdf_nodos, RUTA_HTML_SALIDA)

    print("\n" + "=" * 70)
    print("¡PROCESAMIENTO Y TOPOLOGÍA COMPLETADOS EXITOSAMENTE!")
    print(f" 1. GeoJSON Tramos (Líneas) : {RUTA_GEOJSON_LINEAS.resolve()}")
    print(f" 2. GeoJSON Nodos (Puntos)  : {RUTA_GEOJSON_NODOS.resolve()}")
    print(f" 3. Visualizador Web HTML   : {RUTA_HTML_SALIDA.resolve()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
