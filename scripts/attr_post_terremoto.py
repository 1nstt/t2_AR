"""
=============================================================================
5.0 · ATTR de la infraestructura crítica tras el sismo M 8.8 (27F)
-----------------------------------------------------------------------------
Usa el mismo método del apartado 3.0 (scripts/attr.py) sobre la misma
topología de la BD (red, nodo_clave_vertice), pero con la probabilidad de
falla del sismo (red.prob_falla_8_8, apartado 4.2):

  * Previo (3.1): red en operación, prob_falla = 0  -> cálculo exacto
  * Post 8.8:     prob_falla_8_8                    -> Monte Carlo, N = 20.000
                  (Hoeffding: error <= 0,0096 con 95 % de confianza)

Además identifica los nodos clave y los enlaces más afectados:
  * confiabilidad de cada nodo clave = promedio de R_ij con los otros 54
  * enlaces puente (sin ruta alternativa) que separan nodos clave:
        caída del ATTR si se cortan  x  prob_falla_8_8  = pérdida esperada
  * vértices de la red que pierden acceso a los nodos clave

Salidas: datos/salida/attr_post/  y filas nuevas en la tabla attr_resumen.
Uso:  docker compose run --rm app python scripts/attr_post_terremoto.py
=============================================================================
"""
import math
import sys

import networkx as nx
import numpy as np
import pandas as pd

import attr as A
from db import DIRECTORIO_BASE, conexion, consulta
from nodos_clave import localidad_cercana

SALIDA = DIRECTORIO_BASE / "datos" / "salida" / "attr_post"
ITERACIONES = 20000


def lugar(lon, lat):
    nom, d = localidad_cercana(lon, lat)
    return nom if d < 5 else f"{nom} ({d:.0f} km)"


def mostrar(titulo, df, archivo=None):
    print(f"\n{titulo}\n" + "-" * len(titulo))
    print(df.to_string(index=False))
    if archivo:
        df.to_csv(SALIDA / archivo, index=False, encoding="utf-8-sig")


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    log = open(SALIDA / "ejecucion_attr_post.log", "w", encoding="utf-8")
    sys.stdout = A._Tee(sys.__stdout__, log)
    try:
        con = conexion()
        red = A.leer_red(con)
        extra = consulta(con, """
            SELECT r.id, r.prob_falla_8_8, r.codigo_tipo, r.longitud_km, r.pga_g, r.sintetico,
                   ST_X(ST_Transform(ST_LineInterpolatePoint(r.geom, 0.5), 4326)) lon,
                   ST_Y(ST_Transform(ST_LineInterpolatePoint(r.geom, 0.5), 4326)) lat
            FROM red r ORDER BY r.id""")
        assert (extra["id"].to_numpy() == red["aristas"]["id"].to_numpy()).all()
        p88 = extra["prob_falla_8_8"].astype(float).to_numpy()
        claves = red["claves"]
        n = len(claves)
        todas = np.ones(len(p88), bool)
        print(f"Red de la BD: {len(p88)} aristas, {red['n_vert']} vértices, {n} nodos clave, "
              f"{math.comb(n, 2)} pares | prob_falla_8_8: media {p88.mean():.4f}, máx {p88.max():.4f}")

        # ------------------------------------------------------------------
        # ATTR previo (3.1) y post 8.8, con y sin enlaces sintéticos
        # ------------------------------------------------------------------
        filas, matrices = [], {}
        for nombre, desc, p, masc, metodo in [
            ("operacion", "3.1 · Red en operación (prob_falla = 0)", red["p"], todas, "auto"),
            ("post_terremoto_8_8", "5.0 · Tras el M 8.8 (prob_falla_8_8)", p88, todas, "montecarlo"),
            ("operacion_sin_sinteticos", "Sensibilidad · en operación sin enlaces sintéticos",
             red["p"], ~red["sintetico"], "auto"),
            ("post_terremoto_8_8_sin_sinteticos", "Sensibilidad · tras el M 8.8 sin enlaces sintéticos",
             p88, ~red["sintetico"], "montecarlo"),
        ]:
            fila, pares = A.escenario(con, red, nombre, desc, p, masc, metodo, ITERACIONES, None)
            filas.append(fila)
            matrices[nombre] = pares
        res = pd.DataFrame(filas)
        base = res.set_index("escenario")["attr"]
        res["attr_previo"] = [base["operacion"] if "sin_sint" not in e else base["operacion_sin_sinteticos"]
                              for e in res["escenario"]]
        res["diferencia"] = res["attr"] - res["attr_previo"]
        res["variacion_pct"] = 100 * res["diferencia"] / res["attr_previo"]
        mostrar("RESUMEN: ATTR previo vs post 8.8", res[[
            "escenario", "metodo", "iteraciones", "attr", "ic95_inf", "ic95_sup", "attr_previo",
            "diferencia", "variacion_pct"]].round(6), "attr_post_resumen.csv")

        # ------------------------------------------------------------------
        # Nodos clave más afectados
        # ------------------------------------------------------------------
        def confiab(nombre):
            m = matrices[nombre]
            s = pd.concat([m[["nodo_i", "R_ij"]].rename(columns={"nodo_i": "nombre"}),
                           m[["nodo_j", "R_ij"]].rename(columns={"nodo_j": "nombre"})])
            return s.groupby("nombre")["R_ij"].mean()
        vs = consulta(con, """SELECT n.nombre, n.tipo, n.rol, n.id_vertice, round(s.pga_g::numeric, 3) pga_g,
                                     ST_X(ST_Transform(v.the_geom, 4326)) lon, ST_Y(ST_Transform(v.the_geom, 4326)) lat
                              FROM nodo_clave_vertice n JOIN vertice_sismo s ON s.id = n.id_vertice
                              JOIN red_vertices_pgr v ON v.id = n.id_vertice""")
        vs["lugar"] = [lugar(a, b) for a, b in zip(vs["lon"], vs["lat"])]
        vs["confiab_previa"] = vs["nombre"].map(confiab("operacion")).round(4)
        vs["confiab_post"] = vs["nombre"].map(confiab("post_terremoto_8_8")).round(4)
        vs["perdida"] = (vs["confiab_previa"] - vs["confiab_post"]).round(4)
        vs["perdida_pct"] = (100 * vs["perdida"] / vs["confiab_previa"].replace(0, np.nan)).round(1)
        vs = vs.drop(columns=["lon", "lat"]).sort_values("perdida", ascending=False)
        mostrar("NODOS CLAVE: confiabilidad (promedio de R_ij con los otros 54)", vs, "nodos_clave_afectados.csv")

        par = matrices["operacion"][["nodo_i", "nodo_j", "R_ij"]].rename(columns={"R_ij": "R_previo"})
        par["R_post"] = matrices["post_terremoto_8_8"]["R_ij"].to_numpy()
        par["perdida"] = (par["R_previo"] - par["R_post"]).round(4)
        mostrar("PARES más afectados", par.sort_values("perdida", ascending=False).head(15), "pares_afectados.csv")

        # ------------------------------------------------------------------
        # Enlaces críticos: puentes que separan nodos clave
        # ------------------------------------------------------------------
        src, dst = red["src"], red["dst"]
        G = nx.MultiGraph()
        G.add_nodes_from(range(red["n_vert"]))
        G.add_edges_from((int(a), int(b), int(k)) for k, (a, b) in enumerate(zip(src, dst)))
        simple = nx.Graph(G)
        cuenta = {}
        for a, b in zip(src, dst):
            cuenta[(min(a, b), max(a, b))] = cuenta.get((min(a, b), max(a, b)), 0) + 1
        es_clave = np.zeros(red["n_vert"], int)
        np.add.at(es_clave, red["idx_clave"], 1)          # varios nodos clave pueden compartir vértice
        total = math.comb(n, 2)
        aristas_idx = {}
        for k, (a, b) in enumerate(zip(src, dst)):
            aristas_idx.setdefault((min(a, b), max(a, b)), k)
        puentes = []
        for a, b in nx.bridges(simple):
            key = (min(a, b), max(a, b))
            if cuenta[key] > 1:
                continue
            H = simple.copy()
            H.remove_edge(a, b)
            lado = nx.node_connected_component(H, a)
            comp = nx.node_connected_component(simple, a)
            ka = int(es_clave[list(lado)].sum())
            kb = int(es_clave[list(comp - lado)].sum())
            if ka * kb == 0:
                continue
            k = aristas_idx[key]
            e = extra.iloc[k]
            puentes.append({"id": int(e["id"]), "codigo_tipo": e["codigo_tipo"], "km": round(e["longitud_km"], 2),
                            "lugar": lugar(e["lon"], e["lat"]), "pga_g": round(e["pga_g"], 3),
                            "prob_falla_8_8": round(e["prob_falla_8_8"], 4), "separa": f"{ka} | {kb}",
                            "caida_attr_si_falla": round(ka * kb / total, 4)})
        pu = pd.DataFrame(puentes)
        pu["perdida_esperada_attr"] = (pu["prob_falla_8_8"] * pu["caida_attr_si_falla"]).round(5)
        pu = pu.sort_values("perdida_esperada_attr", ascending=False)
        mostrar(f"ENLACES CRÍTICOS: {len(pu)} puentes que separan nodos clave (top 15)", pu.head(15),
                "enlaces_criticos.csv")
        pu.to_csv(SALIDA / "enlaces_criticos.csv", index=False, encoding="utf-8-sig")

        top = extra.sort_values("prob_falla_8_8", ascending=False).head(12).copy()
        top["lugar"] = [lugar(a, b) for a, b in zip(top["lon"], top["lat"])]
        mostrar("ENLACES con mayor probabilidad de corte (4.2)",
                top[["id", "codigo_tipo", "longitud_km", "lugar", "pga_g", "prob_falla_8_8"]].round(3),
                "enlaces_mayor_prob.csv")

        # ------------------------------------------------------------------
        # Guardar en la BD (tabla attr_resumen del apartado 3.0)
        # ------------------------------------------------------------------
        con.execute("DELETE FROM attr_resumen WHERE escenario LIKE 'post_terremoto%'")
        with con.cursor() as cur:
            cur.executemany("INSERT INTO attr_resumen (escenario, metodo, attr, error_estandar, ic95_inf, "
                            "ic95_sup, iteraciones) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            [(r.escenario, r.metodo, float(r.attr), float(r.error_estandar), float(r.ic95_inf),
                              float(r.ic95_sup), int(r.iteraciones)) for r in res.itertuples()
                             if r.escenario.startswith("post")])
        print("\ndatos/salida/attr_post/ | filas post_terremoto_* en la tabla attr_resumen")
    finally:
        sys.stdout = sys.__stdout__
        log.close()


if __name__ == "__main__":
    main()
