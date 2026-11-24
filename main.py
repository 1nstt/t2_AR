"""
=============================================================================
main.py · T2 Ruteo: todo el flujo con UN SOLO COMANDO
-----------------------------------------------------------------------------
    python main.py          ->  inicia Docker y deja el mapa en http://localhost:8080

Desde el host basta Python 3 (sin dependencias) y Docker: main.py levanta los
contenedores (`db` con PostGIS + pgRouting y `main` con Python 3.12) y se
vuelve a ejecutar a sí mismo dentro del contenedor `main`.

Pasos:
  1. BD      Crea la base `red_chile` VACÍA y carga main.sql (infraestructura:
             topología de pgr_createTopology, nodos clave, sismo de USGS).
  2. P(f)    Calcula la probabilidad de falla de cada enlace tras el M 8.8
             (sql/terremoto/02_modelo_falla.sql: Youngs 1997 + ALA 2001 +
             HAZUS) y la compara con la que trae main.sql.
  3. ATTR    ATTR antes del sismo (prob_falla = 0, exacto, verificado con
             pgr_connectedComponents) y después (prob_falla_8_8, Monte Carlo
             N = 20.000), con la diferencia y la variación en %.
  4. Mapa    Levanta el visor Leaflet sobre `red_chile`: nodos clave, enlaces
             coloreados por P(falla), epicentro, contornos del ShakeMap y
             enlaces sintéticos destacados.

No toca la BD de trabajo (`ruteo`), que generan los scripts de scripts/.
Opciones:  --sin-mapa  --iteraciones N  --bd NOMBRE  --puerto N
Sin Docker (PostGIS + pgRouting en localhost, lee .env):  python main.py --local
=============================================================================
"""
import argparse
import math
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
# scripts/ primero: así `import main` dentro de scripts/ sigue siendo scripts/main.py
sys.path.insert(0, str(RAIZ / "scripts"))
sys.path.insert(1, str(RAIZ))

MAIN_SQL = RAIZ / "main.sql"
SQL_MODELO = RAIZ / "sql" / "terremoto" / "02_modelo_falla.sql"
REFERENCIA_ATTR = RAIZ / "datos" / "salida" / "attr_post" / "attr_post_resumen.csv"


def titulo(n, texto):
    print(f"\n{'=' * 78}\n[{n}/4] {texto}\n{'=' * 78}", flush=True)


def en_docker():
    return os.environ.get("T2_EN_DOCKER") == "1" or Path("/.dockerenv").exists()


def lanzar_docker(argumentos):
    """Desde el host (solo Python estándar, sin dependencias): levanta los contenedores
    y vuelve a ejecutar este mismo main.py dentro del contenedor `main`.
      * db    PostgreSQL 17 + PostGIS 3.5 + pgRouting 3.7.3 (se inicia y se espera a que esté sano)
      * main  Python 3.12 con requirements.txt; publica el mapa en http://localhost:8080"""
    import shutil
    import subprocess

    if not shutil.which("docker"):
        sys.exit("No se encontró `docker`. Instale Docker (Docker Desktop con integración WSL) o use --local.")
    if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        sys.exit("Docker no está corriendo: abra Docker Desktop (o `sudo service docker start`) y reintente.")
    if not (RAIZ / ".env").exists():
        sys.exit("Falta el archivo .env con las credenciales de la base (POSTGRES_DB, POSTGRES_USER, ...).")

    cmd = ["docker", "compose", "run", "--build", "--rm", "--service-ports", "main", "python", "main.py",
           *argumentos]
    print("Iniciando Docker (base PostGIS + pgRouting y entorno Python 3.12)…")
    print("$ " + " ".join(cmd), flush=True)
    try:
        codigo = subprocess.run(cmd, cwd=RAIZ).returncode
    except KeyboardInterrupt:
        codigo = 0
    print("\nPara apagar la base de datos:  docker compose down")
    sys.exit(codigo)


def cargar_env():
    """Fuera de Docker: toma las credenciales de .env si no hay variables PG*."""
    if os.environ.get("PGUSER") or not (RAIZ / ".env").exists():
        return
    env = {}
    for linea in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.lstrip().startswith("#"):
            k, v = linea.split("=", 1)
            env[k.strip()] = v.strip()
    os.environ.setdefault("PGHOST", "localhost")
    os.environ.setdefault("PGPORT", env.get("POSTGRES_PORT", "5432"))
    os.environ.setdefault("PGDATABASE", env.get("POSTGRES_DB", "postgres"))
    os.environ.setdefault("PGUSER", env.get("POSTGRES_USER", "postgres"))
    os.environ.setdefault("PGPASSWORD", env.get("POSTGRES_PASSWORD", ""))


def esperar_bd(conexion, intentos=30):
    for i in range(intentos):
        try:
            return conexion()
        except Exception as e:  # la BD todavía está arrancando
            if i == intentos - 1:
                sys.exit(f"No se pudo conectar a PostgreSQL: {e}")
            time.sleep(2)


def cargar_dump(con, ruta):
    """Ejecuta un archivo de pg_dump (sentencias SQL + bloques COPY ... FROM stdin)
    con psycopg, sin depender de psql."""
    bloques, buf = 0, []
    with ruta.open(encoding="utf-8") as f, con.cursor() as cur:
        lineas = iter(f)
        for linea in lineas:
            if linea.startswith("\\"):            # meta-comandos de psql (\restrict, ...)
                continue
            if linea.startswith("COPY ") and linea.rstrip().endswith("FROM stdin;"):
                if buf:
                    cur.execute("".join(buf))
                    buf = []
                with cur.copy(linea.rstrip().rstrip(";")) as copia:
                    for dato in lineas:
                        if dato.rstrip("\r\n") == "\\.":
                            break
                        copia.write(dato)
                bloques += 1
            else:
                buf.append(linea)
        if buf:
            cur.execute("".join(buf))
    return bloques


def main():
    ap = argparse.ArgumentParser(description="T2 Ruteo: BD + probabilidades + ATTR + mapa Leaflet")
    ap.add_argument("--bd", default=os.environ.get("MAIN_DB", "red_chile"), help="base que se crea desde main.sql")
    ap.add_argument("--iteraciones", type=int, default=20000, help="Monte Carlo del ATTR post 8.8")
    ap.add_argument("--sin-mapa", action="store_true", help="no levantar el visor Leaflet al final")
    ap.add_argument("--puerto", type=int, default=int(os.environ.get("MAIN_PORT", "8000")))
    ap.add_argument("--local", action="store_true",
                    help="no usar Docker: PostGIS + pgRouting en localhost y dependencias instaladas")
    args = ap.parse_args()

    if not args.local and not en_docker():
        lanzar_docker(sys.argv[1:])
        return

    cargar_env()
    import numpy as np
    import attr as A                        # scripts/attr.py (apartado 3.0)
    from db import conexion, consulta       # scripts/db.py

    t0 = time.time()
    print("T2 Ruteo · Red troncal de Internet en Chile ante el M 8.8 (27F)")
    print(f"PostgreSQL: {os.environ.get('PGHOST')}:{os.environ.get('PGPORT')} | base de trabajo: {args.bd}")

    # ------------------------------------------------------------------
    # 1. Base de datos desde main.sql
    # ------------------------------------------------------------------
    titulo(1, f"Carga de main.sql en una base vacía ({args.bd})")
    admin = esperar_bd(conexion)
    admin.execute(f'DROP DATABASE IF EXISTS "{args.bd}" WITH (FORCE)')
    admin.execute(f'CREATE DATABASE "{args.bd}"')
    admin.close()
    os.environ["PGDATABASE"] = args.bd       # desde aquí, todo (scripts y visor) usa esta base

    con = conexion()
    con.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    try:
        con.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
        hay_pgrouting = True
    except Exception:
        hay_pgrouting = False
        print("   (pgRouting no disponible: se omite la verificación con pgr_connectedComponents)")
    bloques = cargar_dump(con, MAIN_SQL)
    con.close()                              # el dump deja search_path vacío en la sesión
    con = conexion()
    print(f"main.sql: {MAIN_SQL.stat().st_size / 1e6:.1f} MB, {bloques} tablas con datos")
    resumen = consulta(con, """
        SELECT (SELECT count(*) FROM red) AS enlaces,
               (SELECT count(*) FROM red WHERE sintetico) AS sinteticos,
               (SELECT count(*) FROM red_vertices_pgr) AS vertices,
               (SELECT count(*) FROM nodo_clave_vertice) AS nodos_clave,
               (SELECT magnitud FROM terremoto) AS sismo_mw""")
    print(resumen.to_string(index=False))
    p_main = consulta(con, "SELECT id, prob_falla_8_8 FROM red ORDER BY id")

    # ------------------------------------------------------------------
    # 2. Probabilidad de falla de cada enlace (4.1 / 4.2)
    # ------------------------------------------------------------------
    titulo(2, "Probabilidad de falla de cada enlace tras el M 8.8")
    print(f"-> {SQL_MODELO.relative_to(RAIZ)} (Youngs 1997 + ALA 2001 + HAZUS, ST_Distance en EPSG:32719)")
    con.execute(SQL_MODELO.read_text(encoding="utf-8"))
    p_nueva = consulta(con, "SELECT id, prob_falla_8_8 FROM red ORDER BY id")
    dif = np.abs(p_nueva["prob_falla_8_8"].to_numpy() - p_main["prob_falla_8_8"].to_numpy()).max()
    print(consulta(con, """
        SELECT codigo_tipo, count(*) AS enlaces, round(sum(longitud_km)::numeric, 1) AS km,
               round(avg(prob_falla_8_8)::numeric, 4) AS p_media, round(max(prob_falla_8_8)::numeric, 4) AS p_max,
               round(sum(prob_falla_8_8)::numeric, 1) AS cortes_esperados
        FROM red GROUP BY 1 ORDER BY cortes_esperados DESC""").to_string(index=False))
    tot = consulta(con, """SELECT count(*) FILTER (WHERE prob_falla_8_8 IS NULL) AS sin_p,
                                  count(*) FILTER (WHERE prob_falla_8_8 < 0 OR prob_falla_8_8 > 1) AS fuera_0_1,
                                  round(sum(prob_falla_8_8)::numeric, 1) AS cortes
                           FROM red""").iloc[0]
    print(f"Total: {float(tot['cortes'])} cortes esperados | sin probabilidad: {tot['sin_p']} | "
          f"fuera de [0, 1]: {tot['fuera_0_1']}")
    print(f"Diferencia máxima con la probabilidad guardada en main.sql: {dif:.2e} "
          f"({'coincide' if dif < 1e-9 else 'NO coincide'})")

    # ------------------------------------------------------------------
    # 3. ATTR antes y después (3.1 y 5.0)
    # ------------------------------------------------------------------
    titulo(3, "ATTR de los nodos clave: antes y después del M 8.8")
    red = A.leer_red(con)
    p88 = consulta(con, "SELECT prob_falla_8_8 FROM red ORDER BY id")["prob_falla_8_8"].astype(float).to_numpy()
    todas = np.ones(len(p88), bool)
    antes, _ = A.escenario(con, red, "operacion", "3.1 · Red en operación (prob_falla = 0)",
                           red["p"], todas, "auto", 0, "" if hay_pgrouting else None)
    despues, _ = A.escenario(con, red, "post_terremoto_8_8", "5.0 · Tras el M 8.8 (prob_falla_8_8)",
                             p88, todas, "montecarlo", args.iteraciones, None)
    with con.cursor() as cur:
        cur.execute("DELETE FROM attr_resumen WHERE escenario IN ('operacion', 'post_terremoto_8_8')")
        cur.executemany("INSERT INTO attr_resumen (escenario, metodo, attr, error_estandar, ic95_inf, ic95_sup, "
                        "iteraciones) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(f["escenario"], f["metodo"], float(f["attr"]), float(f["error_estandar"]),
                          float(f["ic95_inf"]), float(f["ic95_sup"]), int(f["iteraciones"]))
                         for f in (antes, despues)])
    d = despues["attr"] - antes["attr"]
    print(f"\n{'':24}{'ATTR':>10}   IC 95 %")
    print(f"{'Antes (en operación)':24}{antes['attr']:>10.6f}   exacto ({antes.get('pares_conectados')} de "
          f"{antes['pares']} pares conectados)")
    print(f"{'Después (M 8.8)':24}{despues['attr']:>10.6f}   [{despues['ic95_inf']:.6f}, {despues['ic95_sup']:.6f}] "
          f"Monte Carlo N = {args.iteraciones}")
    print(f"{'Diferencia':24}{d:>+10.6f}   ({100 * d / antes['attr']:+.1f} %)")
    if REFERENCIA_ATTR.exists():
        import pandas as pd
        inf = pd.read_csv(REFERENCIA_ATTR, encoding="utf-8-sig").set_index("escenario")["attr"]
        ok = (abs(inf["operacion"] - antes["attr"]) < 1e-6
              and abs(inf["post_terremoto_8_8"] - despues["attr"]) < 1e-6)
        print(f"Valores guardados: antes {inf['operacion']:.6f}, después {inf['post_terremoto_8_8']:.6f} "
              f"-> {'mismos valores' if ok else 'DISTINTOS (revisar)'}")
    con.close()
    print(f"\nTiempo total: {time.time() - t0:.0f} s")

    # ------------------------------------------------------------------
    # 4. Mapa Leaflet
    # ------------------------------------------------------------------
    if args.sin_mapa:
        return
    titulo(4, "Mapa Leaflet")
    url = os.environ.get("MAPA_URL", f"http://localhost:{args.puerto}")
    print(f"Abrir {url}  (Ctrl+C para terminar)")
    print("  · Escenario ' Terremoto 8.8': enlaces coloreados por P(falla), epicentro, plano de ruptura,")
    print("    contornos PGA/MMI del ShakeMap; los enlaces sintéticos se dibujan destacados.")
    print("  · Escenario ' En operación': la red con prob_falla = 0 y los 55 nodos clave.")
    import uvicorn
    uvicorn.run("web.api:app", host=os.environ.get("MAIN_HOST", "127.0.0.1"), port=args.puerto,
                log_level="warning")


if __name__ == "__main__":
    main()
