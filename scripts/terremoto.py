"""
=============================================================================
TERREMOTO · Sismo M 8.8 del Maule (27-02-2010) desde USGS → PostGIS
-----------------------------------------------------------------------------
1. Descarga el GeoJSON del evento desde la API FDSN de USGS de dos formas:
     a) por ID del evento   (eventid=official20100227063411530_30)
     b) por consulta        (27-02-2010, magnitud >= 8,5)
   y comprueba que ambas devuelven el mismo evento.
2. Descarga los contornos de ShakeMap (intensidad MMI y PGA) del mismo evento,
   como insumo para los apartados de fallas.
3. Guarda los archivos originales en datos/entrada/usgs/ (con fecha y SHA-256).
4. Carga las tablas en la BD: terremoto, terremoto_mmi, terremoto_pga,
   usgs_descargas.
Uso:  docker compose run --rm app python scripts/terremoto.py
=============================================================================
"""
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from db import DIRECTORIO_BASE, conexion, consulta

EVENTO_ID = "official20100227063411530_30"
API = "https://earthquake.usgs.gov/fdsnws/event/1/query"
URL_ID = f"{API}?eventid={EVENTO_ID}&format=geojson"
URL_CONSULTA = (f"{API}?format=geojson&starttime=2010-02-27T00:00:00&endtime=2010-02-28T00:00:00"
                f"&minmagnitude=8.5")
PAGINA_EVENTO = f"https://earthquake.usgs.gov/earthquakes/eventpage/{EVENTO_ID}/executive"

ENTRADA = DIRECTORIO_BASE / "datos" / "entrada" / "usgs"
SALIDA = DIRECTORIO_BASE / "datos" / "salida" / "terremoto"


def descargar(url, nombre):
    """Descarga una URL, la guarda en datos/entrada/usgs/ y devuelve (json, registro)."""
    req = urllib.request.Request(url, headers={"User-Agent": "t2-ruteo/1.0 (proyecto academico)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        datos = r.read()
    ruta = ENTRADA / nombre
    ruta.write_bytes(datos)
    reg = {"archivo": f"datos/entrada/usgs/{nombre}", "url": url,
           "descargado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest()}
    print(f"   {nombre}: {len(datos):,} bytes  ({url})")
    return json.loads(datos), reg


def main():
    ENTRADA.mkdir(parents=True, exist_ok=True)
    SALIDA.mkdir(parents=True, exist_ok=True)

    print("=" * 78 + "\n1. Descarga desde la API FDSN de USGS\n" + "=" * 78)
    evento, r1 = descargar(URL_ID, f"evento_{EVENTO_ID}.geojson")
    resultado, r2 = descargar(URL_CONSULTA, "consulta_2010-02-27_M8.5.geojson")
    ids_consulta = [f["id"] for f in resultado["features"]]
    print(f"   Consulta por fecha y magnitud: {resultado['metadata']['count']} evento(s) → {ids_consulta}")
    if ids_consulta != [EVENTO_ID]:
        sys.exit("La consulta no devolvió exactamente el evento esperado.")
    print(f"   La consulta y el ID devuelven el mismo evento ({EVENTO_ID})")

    shakemap = evento["properties"]["products"]["shakemap"][0]   # el preferido (mayor peso)
    contenidos = shakemap["contents"]
    mmi, r3 = descargar(contenidos["download/cont_mmi.json"]["url"], "shakemap_cont_mmi.json")
    pga, r4 = descargar(contenidos["download/cont_pga.json"]["url"], "shakemap_cont_pga.json")
    sm_info = {"fuente": shakemap["source"], "version": shakemap["properties"].get("version"),
               "estado": shakemap["properties"].get("map-status"),
               "procesado": shakemap["properties"].get("process-timestamp"),
               "mmi_max": shakemap["properties"].get("maxmmi")}
    print(f"   ShakeMap usado: {sm_info}")

    print("\n" + "=" * 78 + "\n2. Carga en PostGIS\n" + "=" * 78)
    con = conexion()
    con.execute((DIRECTORIO_BASE / "sql" / "terremoto" / "01_tablas.sql").read_text(encoding="utf-8"))

    p = evento["properties"]
    lon, lat, prof = evento["geometry"]["coordinates"]
    con.execute("""
        INSERT INTO terremoto (id, titulo, lugar, magnitud, tipo_magnitud, fecha_utc, profundidad_km,
            estado, tsunami, significancia, mmi_max, cdi_max, reportes_sentido, alerta, red, ids_asociados,
            url_evento, url_geojson, actualizado_utc, geojson, geom)
        VALUES (%(id)s, %(titulo)s, %(lugar)s, %(mag)s, %(magtype)s, to_timestamp(%(time)s / 1000.0),
            %(prof)s, %(status)s, %(tsunami)s, %(sig)s, %(mmi)s, %(cdi)s, %(felt)s, %(alert)s, %(net)s,
            %(ids)s, %(url)s, %(url_geojson)s, to_timestamp(%(updated)s / 1000.0), %(geojson)s,
            ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326))""",
        {"id": evento["id"], "titulo": p["title"], "lugar": p["place"], "mag": p["mag"], "magtype": p["magType"],
         "time": p["time"], "prof": prof, "status": p["status"], "tsunami": p["tsunami"], "sig": p["sig"],
         "mmi": p.get("mmi"), "cdi": p.get("cdi"), "felt": p.get("felt"), "alert": p.get("alert"),
         "net": p["net"], "ids": p["ids"].strip(","), "url": p["url"], "url_geojson": URL_ID,
         "updated": p["updated"], "geojson": json.dumps(evento), "lon": lon, "lat": lat})

    for tabla, fc in (("terremoto_mmi", mmi), ("terremoto_pga", pga)):
        with con.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {tabla} (evento_id, valor, unidad, color, geom) VALUES "
                f"(%s, %s, %s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))",
                [(EVENTO_ID, f["properties"]["value"], f["properties"].get("units"), f["properties"].get("color"),
                  json.dumps(f["geometry"])) for f in fc["features"]])

    with con.cursor() as cur:
        cur.executemany("INSERT INTO usgs_descargas (archivo, url, descargado_utc, bytes, sha256) "
                        "VALUES (%(archivo)s, %(url)s, %(descargado_utc)s, %(bytes)s, %(sha256)s)",
                        [r1, r2, r3, r4])

    print("\n" + "=" * 78 + "\n3. Tablas en la BD\n" + "=" * 78)
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 80)
    t = consulta(con, """
        SELECT id, titulo, magnitud, tipo_magnitud,
               fecha_utc AT TIME ZONE 'UTC'              AS fecha_utc,
               fecha_utc AT TIME ZONE 'America/Santiago' AS fecha_chile,
               ROUND(ST_Y(geom)::numeric, 3) AS lat, ROUND(ST_X(geom)::numeric, 3) AS lon,
               profundidad_km, estado, mmi_max, cdi_max, reportes_sentido, significancia, red,
               ST_AsText(geom) AS geom_wkt
        FROM terremoto""")
    print(t.T.to_string(header=False))
    t.to_csv(SALIDA / "tabla_terremoto.csv", index=False, encoding="utf-8-sig")

    contornos = consulta(con, """
        SELECT 'MMI' AS capa, COUNT(*) AS contornos, MIN(valor) AS min, MAX(valor) AS max, MIN(unidad) AS unidad
        FROM terremoto_mmi
        UNION ALL
        SELECT 'PGA', COUNT(*), MIN(valor), MAX(valor), MIN(unidad) FROM terremoto_pga""")
    print("\n" + contornos.to_string(index=False))
    contornos.to_csv(SALIDA / "tabla_contornos.csv", index=False, encoding="utf-8-sig")

    desc = consulta(con, "SELECT archivo, bytes, descargado_utc, sha256 FROM usgs_descargas ORDER BY id")
    print("\n" + desc.to_string(index=False))
    desc.to_csv(SALIDA / "descargas.csv", index=False, encoding="utf-8-sig")

    esquema = consulta(con, """
        SELECT column_name AS columna, data_type AS tipo FROM information_schema.columns
        WHERE table_name = 'terremoto' ORDER BY ordinal_position""")
    esquema.to_csv(SALIDA / "esquema_terremoto.csv", index=False, encoding="utf-8-sig")
    (SALIDA / "shakemap_usado.json").write_text(json.dumps(sm_info, indent=2), encoding="utf-8")
    print("\ndatos/entrada/usgs/ (originales) · datos/salida/terremoto/ (tablas y log) · "
          "tablas terremoto, terremoto_mmi, terremoto_pga, usgs_descargas")


class _Tee:
    def __init__(self, *f): self.f = f
    def write(self, s): [x.write(s) for x in self.f]
    def flush(self): [x.flush() for x in self.f]


if __name__ == "__main__":
    SALIDA.mkdir(parents=True, exist_ok=True)
    with open(SALIDA / "ejecucion_terremoto.log", "w", encoding="utf-8") as log:
        sys.stdout = _Tee(sys.__stdout__, log)
        try:
            main()
        finally:
            sys.stdout = sys.__stdout__
