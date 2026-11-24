# T2 Ruteo: resiliencia de la red troncal de Internet en Chile ante el terremoto 8.8

Topología de la red troncal con pgRouting, probabilidad de falla de cada enlace tras el sismo del 27-02-2010 y ATTR de los 55 nodos clave antes (0,9636) y después (0,1309).

## Cómo correr

Requisitos: **Python 3** (cualquier versión, sin instalar paquetes) y **Docker** con Compose corriendo (en Windows, Docker Desktop con integración WSL). Desde la raíz del repositorio:

```bash
python3 main.py
```

[`main.py`](main.py) **inicia Docker por sí solo**: construye las imágenes, levanta la base `db` (PostgreSQL 17 + PostGIS 3.5 + pgRouting 3.7.3), espera a que esté lista y se vuelve a ejecutar dentro del contenedor `main` (Python 3.12 con [`requirements.txt`](requirements.txt)). Ahí hace todo el flujo y al final deja el mapa en **<http://localhost:8080>** (Ctrl+C para terminar; `docker compose down` apaga la base):

| Paso | Qué hace |
| :---: | :--- |
| 1 | Crea la base **`red_chile` vacía** y carga [`main.sql`](main.sql): topología de `pgr_createTopology`, 55 nodos clave y sismo de USGS |
| 2 | **Topología:** la reconstruye desde `datos/salida/infraestructura_limpia.geojson` con `pgr_createTopology` (tolerancia 5 m) y la compara con `main.sql` |
| 3 | **Calcula la probabilidad de falla** de los 4.730 enlaces con [`sql/terremoto/02_modelo_falla.sql`](sql/terremoto/02_modelo_falla.sql) y verifica que coincide con la de `main.sql` |
| 4 | **ATTR antes** (exacto, verificado con `pgr_connectedComponents`) y **después** del 8.8 (Monte Carlo, N = 20.000), con diferencia y % |
| 5 | Levanta el **mapa Leaflet** sobre `red_chile` (nodos clave, enlaces coloreados por P(falla), epicentro, enlaces sintéticos) |

Salida (resumida) de `python3 main.py`:

```
[1/5] Carga de main.sql en una base vacía (red_chile)
main.sql: 4.5 MB, 15 tablas con datos
 enlaces  sinteticos  vertices  nodos_clave sismo_mw
    4730           4      4559           55      8.8
[2/5] Topología con pgr_createTopology (tolerancia 5 m)
Misma topología que main.sql (correspondencia uno a uno); se conserva su numeración de vértices
enlaces (source, target): 4730 filas -> igual a main.sql
vértices (grado): 4559 filas -> igual a main.sql
nodos clave: 55 filas -> igual a main.sql
[3/5] Probabilidad de falla de cada enlace tras el M 8.8
codigo_tipo  enlaces      km p_media  p_max cortes_esperados
      POSTE     1273  6712.3  0.1724 0.7888            219.5
      FIBRA     2041 10312.8  0.0608 0.4949            124.1
     CAMINO     1206  8107.7  0.0349 0.5914             42.1
      FERRY      206  2390.1  0.0240 0.9562              4.9
   CONECTOR        4     3.0  0.0216 0.0754              0.1
Total: 390.7 cortes esperados | sin probabilidad: 0 | fuera de [0, 1]: 0
Diferencia máxima con la probabilidad guardada en main.sql: 0.00e+00 (coincide)
[4/5] ATTR de los nodos clave: antes y después del M 8.8
SQL (pgr_connectedComponents): ATTR = 0.963636 (1431/1485 pares)
                              ATTR   IC 95 %
Antes (en operación)      0.963636   exacto (1431 de 1485 pares conectados)
Después (M 8.8)           0.130923   [0.130523, 0.131324] Monte Carlo N = 20000
Diferencia               -0.832713   (-86.4 %)
Valores guardados: antes 0.963636, después 0.130923 -> mismos valores
Tiempo total: 13 s
[5/5] Mapa Leaflet
Abrir http://localhost:8080  (Ctrl+C para terminar)
```

* **Dependencias:** las instala Docker. Imagen `db`: PostgreSQL 17 + PostGIS 3.5 + pgRouting 3.7.3. Imágenes `main` y `app`: Python 3.12 con [`requirements.txt`](requirements.txt): `geopandas`, `shapely` y `pyproj` (geometrías), `numpy`, `scipy` y `networkx` (grafo y Monte Carlo del ATTR), `psycopg` (PostGIS), `fastapi` y `uvicorn` (servidor del mapa Leaflet), `folium` (mapas estáticos) y `scikit-learn` (DBSCAN). Las credenciales de la base local están en `.env`.
* **Opciones:** `python3 main.py --sin-mapa` (solo resultados, sin levantar el mapa), `--iteraciones N`, `--bd NOMBRE`. Otro puerto: `MAIN_PORT=9000 python3 main.py`. Equivalente sin pasar por el host: `docker compose up --build main`.
* **Sin Docker:** `python main.py --local` usa un PostgreSQL con PostGIS + pgRouting en `localhost` (credenciales de `.env`) y requiere `pip install -r requirements.txt` con Python 3.12 (las versiones fijadas no tienen ruedas para 3.14).
* `main.py` no modifica la base de trabajo `ruteo`: crea `red_chile` desde `main.sql` cada vez que se ejecuta.

### Regenerar todo desde los shapefiles (opcional)

`main.sql` se genera con los scripts de cada apartado, que trabajan sobre la base `ruteo`. Para rehacerlo desde los datos originales:

```bash
docker compose up -d db
docker compose run --rm app                                         # red limpia, nodos y nodos clave (scripts/main.py)
docker compose run --rm app python scripts/topologia.py             # pgr_createTopology
docker compose run --rm app python scripts/attr.py                  # ATTR en operación
docker compose run --rm app python scripts/terremoto.py             # sismo desde USGS
docker compose run --rm app python scripts/modelo_falla.py          # prob_falla_8_8
docker compose run --rm app python scripts/attr_post_terremoto.py   # ATTR post 8.8
bash scripts/generar_main_sql.sh                                    # main.sql + prueba en BD vacía
```

Hay que respetar el orden: cada paso usa lo que deja el anterior en la BD. (`scripts/main.py` es solo la limpieza de la red; el flujo completo es el `main.py` de la raíz.)

## Mapa Leaflet

**Escenario Terremoto 8.8:** enlaces coloreados por su probabilidad de corte (grosor ∝ p), epicentro de USGS (✱), plano de ruptura de 530 × 180 km (rectángulo punteado) y nodos clave. Los enlaces sintéticos se dibujan con línea punteada rosada.

**Escenario En operación:** la red completa por tipo de enlace (`prob_falla` = 0) y los 55 nodos clave coloreados por rol (azul: genera, naranja: intercambia, verde: recibe).

Enlaces directos a estas vistas: `http://localhost:8080/?escenario=terremoto&color=p&vista=-35.8,-72.2,7` y `http://localhost:8080/?escenario=operacion`.

## Detalle del visor

Visor (FastAPI + Leaflet) que lee PostGIS en vivo, en <http://localhost:8080>:

| Pestaña | Qué muestra |
| :--- | :--- |
| **Red** | Los 4.730 enlaces, coloreados por tipo, componente, P(falla) tras el 8.8, PGA o criticidad. Filtros por tipo, componente, rango de probabilidad, PGA, largo, sintéticos y puentes. Capas: vértices de `pgr_createTopology` por grado, nodos clave, epicentro y plano de ruptura, contornos PGA/MMI del ShakeMap y acelerógrafos del 27F |
| **Nodos clave** | Los 55 nodos clave con búsqueda y filtros por rol y tipo, coloreados por rol o por pérdida de confiabilidad tras el 8.8. La ficha muestra el criterio de selección, la fuente (con enlace al registro en TeleGeography o PeeringDB), el vértice asignado, la PGA y la confiabilidad antes y después |
| **Terremoto** | El evento de USGS, la fragilidad por tipo de enlace, los parámetros del modelo con su fuente, la calibración y los archivos descargados |
| **ATTR** | ATTR previo y post 8.8, los puentes críticos (clic para ir al enlace), los totales por tipo, las componentes y los enlaces sintéticos |

* **Escenario** (arriba): **En operación** muestra la red con `prob_falla = 0` y oculta todo lo del sismo; **Terremoto 8.8** activa el epicentro, los contornos, los colores y filtros por probabilidad, PGA y criticidad, y la pérdida de cada nodo clave. Las fichas abiertas se actualizan al cambiar, y el escenario elegido se recuerda al recargar.
* Al hacer clic en un enlace se abre su ficha: atributos de la capa de origen, `source`/`target`, el cálculo completo de P(falla) (distancia → PGA → PGV → K → P), la justificación de K y, si es un puente, cuánto cae el ATTR al cortarse.
* **Mapas estáticos** abre los HTML de `mapas/`.
* API con todos los datos en JSON/GeoJSON: <http://localhost:8080/api/docs>.
* Código en `web/` (`api.py`, `static/`).

## Estructura

```
t2-ruteo/
├── README.md
├── main.py                              ← flujo completo (python3 main.py, inicia Docker)
├── main.sql                             ← infraestructura + probabilidades de falla
├── compose.yaml · Dockerfile · requirements.txt · .env
├── scripts/
│   ├── main.py, nodos_clave.py          ← scripts/main.py: limpieza de la red y mapa folium
│   ├── topologia.py, db.py              ← db.py: conexión a PostGIS
│   ├── attr.py
│   ├── terremoto.py
│   ├── modelo_falla.py
│   ├── generar_main_sql.sh
│   └── attr_post_terremoto.py
├── sql/
│   ├── init/00_extensiones.sql
│   ├── topologia/01…06_*.sql
│   ├── attr/attr_exacto.sql
│   └── terremoto/01_tablas.sql
│       terremoto/02_modelo_falla.sql
├── datos/
│   ├── entrada/                         ← shapefiles originales (scripts/terremoto.py descarga aquí usgs/)
│   └── salida/                          ← red limpia, nodos clave y resultados que usa el visor
├── mapas/                               ← mapa_infraestructura, mapa_nodos_clave, mapa_topologia
└── web/                                 ← visor dinámico: api.py (FastAPI), static/ (Leaflet)
```
