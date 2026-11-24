"""
=============================================================================
SECCIÓN 3.0 · ATTR (Average Two-Terminal Reliability) de los nodos clave
-----------------------------------------------------------------------------
    ATTR = 1 / C(n,2) * sum_{i<j} R_ij

R_ij es la probabilidad de que los nodos clave i y j sigan conectados cuando
cada enlace e falla, de forma independiente, con probabilidad prob_falla(e).

Entrada (desde la base de datos, apartado 2.0):
  * red                 -> aristas (source, target, prob_falla, sintetico)
  * nodo_clave_vertice  -> los 55 nodos clave y su vértice en la topología

Métodos:
  * EXACTO: si todas las probabilidades son 0 o 1, la red es determinista y
    R_ij es 1 o 0. Se calcula en SQL con pgr_connectedComponents
    (sql/attr/attr_exacto.sql) y se verifica también en Python.
  * MONTE CARLO: si algún enlace tiene 0 < p < 1. En cada iteración se
    sortea qué enlaces fallan, se calculan las componentes conexas y se
    cuentan los pares de nodos clave conectados. Por defecto se usan
    N = 20.000 iteraciones, que según la desigualdad de Hoeffding bastan para
    un error de ±0,01 con 95 % de confianza.

Uso:
  docker compose run --rm app python scripts/attr.py                  # red en operación
  docker compose run --rm app python scripts/attr.py --prueba         # validación con un grafo pequeño
  docker compose run --rm app python scripts/attr.py --p-uniforme 0.01 --iteraciones 20000
=============================================================================
"""
import argparse
import itertools
import math
import sys
import time

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from db import DIRECTORIO_BASE, conexion, consulta

SALIDA = DIRECTORIO_BASE / "datos" / "salida" / "attr"
SQL_EXACTO = DIRECTORIO_BASE / "sql" / "attr" / "attr_exacto.sql"
EPSILON, DELTA = 0.01, 0.05   # error máximo y nivel de riesgo (95 % de confianza)
SEMILLA = 20261122


# -----------------------------------------------------------------------------
# Tamaño de muestra
# -----------------------------------------------------------------------------
def iteraciones_hoeffding(eps=EPSILON, delta=DELTA):
    """Hoeffding (1963): para X_k en [0,1] independientes,
    P(|media - ATTR| >= eps) <= 2 exp(-2 N eps^2). Despejando N:
    N >= ln(2/delta) / (2 eps^2)."""
    return math.ceil(math.log(2 / delta) / (2 * eps ** 2))


# -----------------------------------------------------------------------------
# Núcleo de cálculo (independiente de la base de datos)
# -----------------------------------------------------------------------------
def pares_conectados(etiquetas_clave):
    """Número de pares de nodos clave en la misma componente: sum_c C(k_c, 2)."""
    _, k = np.unique(etiquetas_clave, return_counts=True)
    return int((k * (k - 1) // 2).sum())


def componentes(n_vert, src, dst):
    g = coo_matrix((np.ones(len(src), dtype=np.int8), (src, dst)), shape=(n_vert, n_vert)).tocsr()
    return connected_components(g, directed=False)[1]


def attr_exacto(n_vert, src, dst, p, idx_clave):
    """Red determinista (p en {0,1}): los enlaces con p = 1 están caídos."""
    vivo = p < 1
    et = componentes(n_vert, src[vivo], dst[vivo])[idx_clave]
    n = len(idx_clave)
    conectados = pares_conectados(et)
    matriz = (et[:, None] == et[None, :]).astype(float)
    return conectados / math.comb(n, 2), matriz


def attr_monte_carlo(n_vert, src, dst, p, idx_clave, iteraciones, semilla=SEMILLA):
    """Estimador de Monte Carlo del ATTR con fallas independientes por enlace.
    Devuelve: estimación, error estándar, matriz R_ij estimada y las muestras."""
    rng = np.random.default_rng(semilla)
    n = len(idx_clave)
    total_pares = math.comb(n, 2)
    seguro = p <= 0                  # nunca fallan
    aleatorio = (p > 0) & (p < 1)    # se sortean
    s_fijo, d_fijo = src[seguro], dst[seguro]
    s_al, d_al, p_al = src[aleatorio], dst[aleatorio], p[aleatorio]
    muestras = np.empty(iteraciones)
    cuenta = np.zeros((n, n))
    for it in range(iteraciones):
        sobrevive = rng.random(len(p_al)) >= p_al
        et = componentes(n_vert,
                         np.concatenate([s_fijo, s_al[sobrevive]]),
                         np.concatenate([d_fijo, d_al[sobrevive]]))[idx_clave]
        muestras[it] = pares_conectados(et) / total_pares
        cuenta += et[:, None] == et[None, :]
    est = muestras.mean()
    ee = muestras.std(ddof=1) / math.sqrt(iteraciones) if iteraciones > 1 else float("nan")
    return est, ee, cuenta / iteraciones, muestras


def attr_enumeracion(n_vert, src, dst, p, idx_clave):
    """ATTR exacto por enumeración de los 2^m estados (solo para grafos pequeños)."""
    m = len(p)
    total = 0.0
    for estado in itertools.product([False, True], repeat=m):
        vivo = np.array(estado)
        prob = np.prod(np.where(vivo, 1 - p, p))
        if prob == 0:
            continue
        et = componentes(n_vert, src[vivo], dst[vivo])[idx_clave]
        total += prob * pares_conectados(et) / math.comb(len(idx_clave), 2)
    return total


# -----------------------------------------------------------------------------
# Validación con un grafo de prueba
# -----------------------------------------------------------------------------
def prueba():
    """Red "puente" de 4 nodos y 5 enlaces (s-a, s-b, a-b, a-t, b-t), un ejemplo
    clásico de confiabilidad. Se compara Monte Carlo con la enumeración exacta."""
    print("=" * 78 + "\nVALIDACIÓN: red puente (4 nodos, 5 enlaces)\n" + "=" * 78)
    src = np.array([0, 0, 1, 1, 2])
    dst = np.array([1, 2, 2, 3, 3])
    clave = np.array([0, 1, 2, 3])
    n_it = iteraciones_hoeffding()
    filas = []
    for p_val in [0.0, 0.05, 0.1, 0.3, 0.5]:
        p = np.full(5, p_val)
        exacto = attr_enumeracion(4, src, dst, p, clave)
        r_st_exacto = attr_enumeracion(4, src, dst, p, np.array([0, 3]))
        est, ee, R, _ = attr_monte_carlo(4, src, dst, p, clave, n_it)
        q = 1 - p_val
        r_st_formula = 2 * q**2 + 2 * q**3 - 5 * q**4 + 2 * q**5
        filas.append({"p": p_val, "ATTR_exacto": round(exacto, 5), "ATTR_montecarlo": round(est, 5),
                      "error_abs": round(abs(est - exacto), 5), "error_estandar": round(ee, 5),
                      "dentro_de_eps": abs(est - exacto) <= EPSILON,
                      "R_st_enumeracion": round(r_st_exacto, 5), "R_st_formula": round(r_st_formula, 5),
                      "R_st_montecarlo": round(R[0, 3], 5)})
    df = pd.DataFrame(filas)
    print(f"Iteraciones de Monte Carlo: {n_it}")
    print(df.to_string(index=False))
    ok = df["dentro_de_eps"].all() and np.allclose(df["R_st_enumeracion"], df["R_st_formula"], atol=1e-5)
    print("RESULTADO:", "OK" if ok else "FALLA")
    return df, ok


# -----------------------------------------------------------------------------
# Red de la base de datos
# -----------------------------------------------------------------------------
def leer_red(con):
    aristas = consulta(con, "SELECT id, source, target, prob_falla, sintetico FROM red ORDER BY id")
    claves = consulta(con, "SELECT nombre, tipo, rol, id_vertice, componente FROM nodo_clave_vertice ORDER BY nombre")
    vert = np.union1d(aristas["source"].to_numpy(), aristas["target"].to_numpy())
    vert = np.union1d(vert, claves["id_vertice"].to_numpy())
    pos = {v: i for i, v in enumerate(vert)}
    return {
        "aristas": aristas, "claves": claves, "n_vert": len(vert),
        "src": aristas["source"].map(pos).to_numpy(), "dst": aristas["target"].map(pos).to_numpy(),
        "p": aristas["prob_falla"].astype(float).to_numpy(),
        "sintetico": aristas["sintetico"].astype(bool).to_numpy(),
        "idx_clave": claves["id_vertice"].map(pos).to_numpy(),
    }


def attr_sql(con, filtro=""):
    sql = SQL_EXACTO.read_text(encoding="utf-8").replace("{filtro_aristas}", filtro)
    return consulta(con, sql).iloc[0]


def tabla_pares(claves, R):
    filas = []
    n = len(claves)
    for i in range(n):
        for j in range(i + 1, n):
            filas.append({"nodo_i": claves.loc[i, "nombre"], "nodo_j": claves.loc[j, "nombre"],
                          "vertice_i": claves.loc[i, "id_vertice"], "vertice_j": claves.loc[j, "id_vertice"],
                          "R_ij": round(float(R[i, j]), 6)})
    return pd.DataFrame(filas)


def escenario(con, red, nombre, descripcion, p, mascara, metodo, iteraciones, filtro_sql=""):
    print(f"\n{'=' * 78}\nESCENARIO: {nombre}\n{descripcion}\n{'=' * 78}")
    src, dst = red["src"][mascara], red["dst"][mascara]
    p = p[mascara]
    n_al = int(((p > 0) & (p < 1)).sum())
    if metodo == "auto":
        metodo = "exacto" if n_al == 0 else "montecarlo"
    print(f"Aristas: {mascara.sum()} | con 0<p<1: {n_al} | caídas (p=1): {(p >= 1).sum()} | método: {metodo}")
    t0 = time.time()
    fila = {"escenario": nombre, "descripcion": descripcion, "aristas": int(mascara.sum()),
            "aristas_aleatorias": n_al, "n_nodos_clave": len(red["idx_clave"]),
            "pares": math.comb(len(red["idx_clave"]), 2), "metodo": metodo}
    if metodo == "exacto":
        if n_al:
            sys.exit("El método exacto exige probabilidades 0 o 1; use Monte Carlo.")
        valor, R = attr_exacto(red["n_vert"], src, dst, p, red["idx_clave"])
        sql = attr_sql(con, filtro_sql) if filtro_sql is not None else None
        fila.update({"attr": valor, "error_estandar": 0.0, "ic95_inf": valor, "ic95_sup": valor,
                     "iteraciones": 0, "pares_conectados": int(round(valor * fila["pares"]))})
        if sql is not None:
            fila["attr_sql_pgrouting"] = float(sql["attr"])
            fila["coincide_sql"] = abs(float(sql["attr"]) - valor) < 1e-12
            print(f"SQL (pgr_connectedComponents): ATTR = {float(sql['attr']):.6f} "
                  f"({int(sql['pares_conectados'])}/{int(sql['pares'])} pares)")
    else:
        valor, ee, R, muestras = attr_monte_carlo(red["n_vert"], src, dst, p, red["idx_clave"], iteraciones)
        fila.update({"attr": valor, "error_estandar": ee, "ic95_inf": valor - 1.96 * ee,
                     "ic95_sup": valor + 1.96 * ee, "iteraciones": iteraciones,
                     "cota_hoeffding_eps": math.sqrt(math.log(2 / DELTA) / (2 * iteraciones)),
                     "pares_conectados": None})
    fila["segundos"] = round(time.time() - t0, 1)
    print(f"ATTR = {fila['attr']:.6f}  (IC95 % [{fila['ic95_inf']:.6f}, {fila['ic95_sup']:.6f}], "
          f"{fila['segundos']} s)")
    pares = tabla_pares(red["claves"], R)
    pares.insert(0, "escenario", nombre)
    return fila, pares


class _Tee:
    def __init__(self, *f): self.f = f
    def write(self, s): [x.write(s) for x in self.f]
    def flush(self): [x.flush() for x in self.f]


def main():
    ap = argparse.ArgumentParser(description="ATTR de los nodos clave")
    ap.add_argument("--prueba", action="store_true", help="solo la validación con la red puente")
    ap.add_argument("--iteraciones", type=int, default=20000)
    ap.add_argument("--p-uniforme", type=float, default=0.01,
                    help="probabilidad del escenario ilustrativo de Monte Carlo (no cambia la BD)")
    args = ap.parse_args()

    SALIDA.mkdir(parents=True, exist_ok=True)
    log = open(SALIDA / "ejecucion_attr.log", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log)
    try:
        print(f"Hoeffding: N >= ln(2/{DELTA})/(2*{EPSILON}^2) = {iteraciones_hoeffding()} "
              f"-> se usan {args.iteraciones} iteraciones (semilla {SEMILLA})")
        df_prueba, ok = prueba()
        df_prueba.to_csv(SALIDA / "validacion_red_puente.csv", index=False, encoding="utf-8-sig")
        if args.prueba:
            return

        con = conexion()
        red = leer_red(con)
        print(f"\nRed leída de la BD: {len(red['p'])} aristas, {red['n_vert']} vértices, "
              f"{len(red['idx_clave'])} nodos clave en {len(np.unique(red['idx_clave']))} vértices distintos")
        print(f"prob_falla en la BD: min {red['p'].min()}, max {red['p'].max()}")
        todas = np.ones(len(red["p"]), dtype=bool)

        resultados, pares = [], []
        for args_esc in [
            ("operacion", "Red en operación: topología de la BD con prob_falla de cada enlace",
             red["p"], todas, "auto", args.iteraciones, ""),
            ("operacion_sin_sinteticos", "Misma red sin los 4 enlaces sintéticos (solo datos)",
             red["p"], ~red["sintetico"], "auto", args.iteraciones, "AND NOT sintetico"),
            ("operacion_montecarlo", "Red en operación calculada con Monte Carlo (debe coincidir con el exacto)",
             red["p"], todas, "montecarlo", args.iteraciones, None),
            (f"ilustrativo_p{args.p_uniforme}",
             f"ILUSTRATIVO: todos los enlaces con prob_falla = {args.p_uniforme} (no modifica la BD)",
             np.full(len(red["p"]), args.p_uniforme), todas, "montecarlo", args.iteraciones, None),
        ]:
            fila, par = escenario(con, red, *args_esc[:2], *args_esc[2:5], args_esc[5], args_esc[6])
            resultados.append(fila)
            pares.append(par)

        res = pd.DataFrame(resultados)
        res.to_csv(SALIDA / "attr_resumen.csv", index=False, encoding="utf-8-sig")
        todos_pares = pd.concat(pares, ignore_index=True)
        todos_pares.to_csv(SALIDA / "attr_pares.csv", index=False, encoding="utf-8-sig")

        # Pares desconectados en la red en operación (R_ij = 0)
        op = todos_pares[todos_pares["escenario"] == "operacion"]
        desc = op[op["R_ij"] < 1]
        print(f"\nPares no conectados en la red en operación: {len(desc)}")
        print(desc.groupby("nodo_i").size().add(desc.groupby("nodo_j").size(), fill_value=0)
              .sort_values(ascending=False).head(5).to_string())
        # Pares con menor R_ij en el escenario ilustrativo
        il = todos_pares[todos_pares["escenario"].str.startswith("ilustrativo")]
        print("\nPares con menor R_ij en el escenario ilustrativo:")
        print(il.nsmallest(10, "R_ij").to_string(index=False))

        # Guardar en la BD para los apartados siguientes
        con.execute("DROP TABLE IF EXISTS attr_resumen")
        con.execute("""CREATE TABLE attr_resumen (escenario text, metodo text, attr double precision,
                       error_estandar double precision, ic95_inf double precision, ic95_sup double precision,
                       iteraciones int, calculado timestamptz DEFAULT now())""")
        with con.cursor() as cur:
            cur.executemany("INSERT INTO attr_resumen (escenario, metodo, attr, error_estandar, ic95_inf, "
                            "ic95_sup, iteraciones) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            [(r.escenario, r.metodo, float(r.attr), float(r.error_estandar), float(r.ic95_inf),
                              float(r.ic95_sup), int(r.iteraciones)) for r in res.itertuples()])
        print("\n" + res.drop(columns=["descripcion"]).to_string(index=False))
        print("\ndatos/salida/attr/: attr_resumen.csv, attr_pares.csv, validacion_red_puente.csv, "
              "ejecucion_attr.log | tabla attr_resumen en la BD")
    finally:
        sys.stdout = sys.__stdout__
        log.close()


if __name__ == "__main__":
    main()
