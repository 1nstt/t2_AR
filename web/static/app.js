/* Visor dinámico de la red troncal · consume la API de web/api.py */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const api = (ruta) => fetch(ruta).then((r) => { if (!r.ok) throw new Error(`${ruta}: ${r.status}`); return r.json(); });
const fmt = (x, d = 2) => (x === null || x === undefined || x === "" || Number.isNaN(+x)) ? "–"
  : (+x).toLocaleString("es-CL", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (x, d = 1) => x === null || x === undefined ? "–" : fmt(x * 100, d) + " %";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------------------------------------------------------------------------
// Paletas
// ---------------------------------------------------------------------------
const COL_TIPO = { FIBRA: "#2563eb", POSTE: "#ea580c", CAMINO: "#8b7355", FERRY: "#0d9488", CONECTOR: "#db2777" };
const NOM_TIPO = { FIBRA: "Fibra óptica", POSTE: "Postación eléctrica", CAMINO: "Camino sin postación", FERRY: "Ferry", CONECTOR: "Conector sintético" };
const COL_ROL = { GENERA: "#2563eb", INTERCAMBIA: "#ea580c", RECIBE: "#16a34a" };
const COL_COMP = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ca8a04", "#0891b2", "#be185d"];
const ESC_P = { cortes: [0.01, 0.05, 0.1, 0.2, 0.4, 0.6], colores: ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027", "#7f0000"] };
const ESC_PGA = { cortes: [0.02, 0.05, 0.1, 0.2, 0.3, 0.45], colores: ["#c6dbef", "#9ecae1", "#fdd49e", "#fdae6b", "#f16913", "#d94801", "#7f2704"] };
const ESC_PERD = { cortes: [25, 50, 75, 90, 99], colores: ["#1a9850", "#a6d96a", "#fee08b", "#fdae61", "#f46d43", "#a50026"] };
const escala = (e, v) => { let i = 0; while (i < e.cortes.length && v >= e.cortes[i]) i++; return e.colores[i]; };
const etiquetasEscala = (e, f) => e.colores.map((c, i) => [c,
  i === 0 ? `< ${f(e.cortes[0])}` : i === e.cortes.length ? `≥ ${f(e.cortes[i - 1])}` : `${f(e.cortes[i - 1])} – ${f(e.cortes[i])}`]);

// ---------------------------------------------------------------------------
// Estado
// ---------------------------------------------------------------------------
const E = {
  red: null, vert: null, nodos: null, sismo: null, resumen: null, modelo: null,
  modo: "tipo", nModo: "rol",
  f: { tipos: new Set(Object.keys(COL_TIPO)), comp: "", pmin: 0, pmax: 1, pga: 0, km: 0, sint: false, puente: false },
  grados: new Set([1, 3]),
  n: { roles: new Set(Object.keys(COL_ROL)), tipos: new Set(), q: "", lineas: true },
  compNombres: {},
  // ?escenario=operacion|terremoto en la URL manda sobre el escenario guardado
  sismoOn: (() => {
    const q = new URLSearchParams(location.search).get("escenario");
    if (q) return q !== "operacion";
    try { return localStorage.getItem("escenario") !== "operacion"; } catch { return true; }
  })(),
  ultima: null,        // última ficha abierta, para rehacerla al cambiar de escenario
};
const MODOS_SISMO = ["p", "pga", "crit"];

// ---------------------------------------------------------------------------
// Mapa
// ---------------------------------------------------------------------------
const oscuro = matchMedia("(prefers-color-scheme: dark)").matches;
const mapa = L.map("mapa", { preferCanvas: true, zoomControl: true }).setView([-36, -71.5], 5);
const esri = (s, max = 16) => L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/${s}/MapServer/tile/{z}/{y}/{x}`,
  { attribution: "© Esri, HERE, Garmin, © OpenStreetMap", maxZoom: max });
const BASES = {
  "Gris claro": esri("Canvas/World_Light_Gray_Base"),
  "Gris oscuro": esri("Canvas/World_Dark_Gray_Base"),
  "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 19 }),
  "Topográfico": esri("World_Topo_Map", 19),
  "Satélite": esri("World_Imagery", 19),
};
BASES[oscuro ? "Gris oscuro" : "Gris claro"].addTo(mapa);
L.control.layers(BASES, null, { position: "topright" }).addTo(mapa);
L.control.scale({ imperial: false }).addTo(mapa);

// Todo lo que se puede clicar va en UN solo canvas: con varios canvas apilados, el de arriba
// se come los clics aunque no haya nada dibujado en ese punto. El orden de dibujo (y de clic)
// se fija en ordenarCapas(). Las capas decorativas van en panes sin eventos de puntero.
const rend = L.canvas({ tolerance: 5 });
["lineas", "sel"].forEach((n, i) => {
  const pane = mapa.createPane(n);
  pane.style.zIndex = 450 + i * 10;
  pane.style.pointerEvents = "none";
});
const rendRed = rend, rendVert = rend, rendNodos = rend;

const capaRed = L.geoJSON(null, { renderer: rendRed, style: estiloEnlace, onEachFeature: (f, l) => {
  l.on("click", () => abrirEnlace(f.properties.id));
  l.bindTooltip(() => tipEnlace(f.properties), { sticky: true });
} }).addTo(mapa);
const capaVert = L.geoJSON(null, {
  pointToLayer: (f, ll) => L.circleMarker(ll, { renderer: rendVert, radius: f.properties.grado >= 3 ? 3.5 : 2.5, weight: 0.5,
    color: "#111", fillColor: f.properties.grado === 1 ? "#64748b" : f.properties.grado === 2 ? "#cbd5e1" : "#dc2626", fillOpacity: 0.9 }),
  onEachFeature: (f, l) => { l.on("click", () => abrirVertice(f.properties.id)); l.bindTooltip(`Vértice ${f.properties.id} · grado ${f.properties.grado}`); },
});
const capaLineas = L.layerGroup();
const capaNodos = L.featureGroup().addTo(mapa);
const capaEpi = L.featureGroup().addTo(mapa);
const capaPga = L.featureGroup();
const capaMmi = L.featureGroup();
const capaEst = L.featureGroup();
const capaSel = L.layerGroup().addTo(mapa);

// Orden en el canvas compartido: ruptura y contornos abajo, luego enlaces, vértices, estaciones y nodos clave arriba
function ordenarCapas() {
  [capaRed, capaPga, capaMmi].forEach((c) => mapa.hasLayer(c) && c.bringToBack());
  capaEpi.eachLayer((l) => l.bringToBack && l.bringToBack());
  [capaVert, capaEst, capaNodos].forEach((c) => mapa.hasLayer(c) && c.bringToFront());
}

// ---------------------------------------------------------------------------
// Enlaces: estilo, filtro, leyenda
// ---------------------------------------------------------------------------
function colorEnlace(p) {
  switch (E.modo) {
    case "tipo": return COL_TIPO[p.tipo] || "#888";
    case "comp": { const i = E.compOrden.indexOf(p.comp); return i >= 0 && i < COL_COMP.length ? COL_COMP[i] : "#94a3b8"; }
    case "p": return escala(ESC_P, p.p ?? 0);
    case "pga": return escala(ESC_PGA, p.pga ?? 0);
    case "crit": return p.puente ? escala({ cortes: [0.01, 0.05, 0.1, 0.2], colores: ["#fdd49e", "#fc8d59", "#ef6548", "#d7301f", "#7f0000"] }, p.perdida) : (oscuro ? "#3b4452" : "#cbd5e1");
  }
}
function estiloEnlace(f) {
  const p = f.properties;
  let w = 2;
  if (E.modo === "p") w = 1.5 + 4 * (p.p ?? 0);
  if (E.modo === "crit") w = p.puente ? 2.5 + 30 * p.perdida : 1.2;
  if (p.sint) w = Math.max(w, 4);
  return { color: colorEnlace(p), weight: Math.min(w, 9), opacity: 0.9, dashArray: p.sint ? "6 5" : null };
}
function pasaFiltro(p) {
  const f = E.f;
  if (!f.tipos.has(p.tipo)) return false;
  if (f.comp === "C1" && p.comp_nombre !== "C1") return false;
  if (f.comp === "otras" && p.comp_nombre === "C1") return false;
  if (f.comp && f.comp !== "C1" && f.comp !== "otras" && p.comp_nombre !== f.comp) return false;
  if (p.km < f.km) return false;
  if (f.sint && !p.sint) return false;
  if (!E.sismoOn) return true;          // los filtros del terremoto no aplican en operación
  const pr = p.p ?? 0;
  if (pr < f.pmin || pr > f.pmax) return false;
  if ((p.pga ?? 0) < f.pga) return false;
  if (f.puente && !p.puente) return false;
  return true;
}
function dibujarRed() {
  const vis = E.red.features.filter((f) => pasaFiltro(f.properties));
  capaRed.clearLayers();
  capaRed.addData(vis);
  ordenarCapas();
  const km = vis.reduce((s, f) => s + f.properties.km, 0);
  const cortes = vis.reduce((s, f) => s + (f.properties.p ?? 0), 0);
  $("#cont-red").innerHTML = `<b>${vis.length.toLocaleString("es-CL")}</b> de ${E.red.features.length.toLocaleString("es-CL")} enlaces visibles · ${fmt(km, 0)} km<br>`
    + (E.sismoOn
      ? `Cortes esperados tras el 8.8 (Σ p): <b>${fmt(cortes, 1)}</b> · puentes: ${vis.filter((f) => f.properties.puente).length}`
      : `En operación: <code>prob_falla</code> = 0 en todos los enlaces (2.0) · 0 cortes esperados`);
  leyendaRed();
}
function leyendaRed() {
  let filas = [];
  if (E.modo === "tipo") filas = Object.keys(COL_TIPO).map((t) => [COL_TIPO[t], NOM_TIPO[t]]);
  if (E.modo === "comp") filas = [...E.compOrden.slice(0, COL_COMP.length).map((c, i) => [COL_COMP[i], E.compNombres[c] + (i === 0 ? " (principal, 98,6 % de los km)" : "")]), ["#94a3b8", "Otras componentes aisladas"]];
  if (E.modo === "p") filas = etiquetasEscala(ESC_P, (v) => fmt(v, 2));
  if (E.modo === "pga") filas = etiquetasEscala(ESC_PGA, (v) => fmt(v, 2) + " g");
  if (E.modo === "crit") filas = [["#fdd49e", "Puente, pérdida esperada < 0,01"], ["#ef6548", "Puente, 0,05 – 0,1"], ["#7f0000", "Puente, ≥ 0,2 (corredor Casablanca–San Antonio)"], [oscuro ? "#3b4452" : "#cbd5e1", "No es puente (hay ruta alternativa)"]];
  const extra = { p: "Probabilidad de corte de cada enlace tras el M 8.8 (4.2). Grosor ∝ p.", pga: "Aceleración máxima del suelo en el punto medio del enlace (Youngs 1997 calibrada).", crit: "Grosor ∝ pérdida esperada de ATTR (5.0)." }[E.modo];
  $("#leyenda").innerHTML = filas.map(([c, t]) => `<div class="fila"><span class="sw" style="background:${c}"></span>${esc(t)}</div>`).join("")
    + `<div class="fila"><span class="sw" style="background:repeating-linear-gradient(90deg,#db2777 0 5px,transparent 5px 9px)"></span>Enlace sintético (línea punteada)</div>`
    + (extra ? `<p class="nota">${extra}</p>` : "");
}
function tipEnlace(p) {
  const base = `<b>Enlace ${p.id}</b> · ${NOM_TIPO[p.tipo]} · ${fmt(p.km, 1)} km · ${esc(p.comp_nombre)}`;
  if (!E.sismoOn) return base + `<br>P(falla) en operación: 0`;
  return base + `<br>P(falla) 8.8: <b>${fmt(p.p, 3)}</b> · PGA ${fmt(p.pga, 2)} g`
    + (p.puente ? `<br>Puente crítico (pérdida esperada ${fmt(p.perdida, 3)})` : "");
}

// ---------------------------------------------------------------------------
// Nodos clave
// ---------------------------------------------------------------------------
function colorNodo(p) {
  return E.nModo === "rol" ? COL_ROL[p.rol] : (p.perdida_pct === null ? "#94a3b8" : escala(ESC_PERD, p.perdida_pct));
}
function nodoVisible(p) {
  const q = E.n.q.trim().toLowerCase();
  return E.n.roles.has(p.rol) && E.n.tipos.has(p.tipo)
    && (!q || [p.nombre, p.detalle, p.lugar, p.tipo, String(p.id_vertice)].join(" ").toLowerCase().includes(q));
}
const FORMA = { "Aterrizaje cable submarino": "▼", "IXP / PIT": "⇄", "Data center": "■", "Nodo troncal": "◆" };
function dibujarNodos() {
  capaNodos.clearLayers(); capaLineas.clearLayers();
  const vis = E.nodos.features.filter((f) => nodoVisible(f.properties));
  vis.forEach((f) => {
    const p = f.properties, c = colorNodo(p);
    const ll = [p.lat, p.lon];
    const m = L.circleMarker(ll, { renderer: rendNodos, radius: p.tipo === "Nodo troncal" ? 6 : 7.5, color: "#fff", weight: 2, fillColor: c, fillOpacity: 1 });
    m.bindTooltip(`${FORMA[p.tipo] || ""} <b>${esc(p.nombre)}</b><br>${esc(p.tipo)} · ${p.rol}`
      + (p.perdida_pct === null ? "" : E.sismoOn
        ? `<br>Confiabilidad: ${fmt(p.confiab_previa, 3)} → <b>${fmt(p.confiab_post, 3)}</b>`
        : `<br>Confiabilidad en operación: ${fmt(p.confiab_previa, 3)}`));
    m.on("click", () => clicNodo(p));
    capaNodos.addLayer(m);
    if (E.n.lineas && p.v_lat !== null) capaLineas.addLayer(L.polyline([ll, [p.v_lat, p.v_lon]], { pane: "lineas", color: c, weight: 1.3, dashArray: "3 4", opacity: 0.8, interactive: false }));
  });
  if (E.n.lineas && $("#c-nodos").checked) capaLineas.addTo(mapa); else mapa.removeLayer(capaLineas);
  ordenarCapas();
  $("#cont-nodos").innerHTML = `<b>${vis.length}</b> de ${E.nodos.features.length} nodos clave visibles`;
  $("#n-lista").innerHTML = vis.map((f) => {
    const p = f.properties;
    return `<li data-i="${p.id}"><span class="pt" style="background:${colorNodo(p)}"></span><div>${esc(p.nombre)}<small>${esc(p.tipo)} · ${p.rol} · vértice ${p.id_vertice}</small></div>`
      + `<div class="der">${p.perdida_pct === null ? "" : E.sismoOn
        ? `<b>−${fmt(p.perdida_pct, 0)} %</b><small>tras el 8.8</small>`
        : `<b>${fmt(p.confiab_previa, 3)}</b><small>confiabilidad</small>`}</div></li>`;
  }).join("");
  $$("#n-lista li").forEach((li) => li.onclick = () => {
    const p = E.nodos.features[+li.dataset.i].properties;
    mapa.setView([p.lat, p.lon], Math.max(mapa.getZoom(), 10)); abrirNodo(p);
  });
  const ley = E.nModo === "rol"
    ? Object.entries(COL_ROL).map(([r, c]) => [c, r])
    : [...etiquetasEscala(ESC_PERD, (v) => v + " %"), ["#94a3b8", "Sin dato"]];
  $("#n-leyenda").innerHTML = ley.map(([c, t]) => `<div class="fila"><span class="sw pt" style="background:${c}"></span>${esc(t)}</div>`).join("")
    + (E.nModo === "perdida" ? `<p class="nota">Pérdida de confiabilidad = caída del promedio de R<sub>ij</sub> con los otros 54 nodos clave (5.0).</p>` : "");
}

// ---------------------------------------------------------------------------
// Fichas
// ---------------------------------------------------------------------------
function ficha(html) {
  $("#ficha-cuerpo").innerHTML = html; $("#ficha").hidden = false;
  $("#ficha").scrollTop = 0;
  setTimeout(() => mapa.invalidateSize(), 50);
}
$("#ficha-cerrar").onclick = () => { $("#ficha").hidden = true; capaSel.clearLayers(); setTimeout(() => mapa.invalidateSize(), 50); };
const dl = (pares) => `<dl>${pares.filter(([, v]) => v !== undefined).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
const barraP = (p, c) => `<div class="barra-p"><i style="width:${Math.min(100, (p ?? 0) * 100)}%;background:${c}"></i></div>`;

async function abrirEnlace(id) {
  E.ultima = () => abrirEnlace(id);
  const e = await api(`/api/red/${id}`);
  capaSel.clearLayers();
  const f = E.red.features.find((x) => x.properties.id === id);
  if (f) L.geoJSON(f, { pane: "sel", interactive: false, style: { color: "#facc15", weight: 9, opacity: 0.55 } }).addTo(capaSel);
  const c = e.critico;
  const cabecera = `
    <span class="etiqueta" style="background:${COL_TIPO[e.codigo_tipo]}">${NOM_TIPO[e.codigo_tipo]}</span>
    ${e.sintetico ? '<span class="etiqueta" style="background:#db2777">SINTÉTICO</span>' : ""}
    ${c && E.sismoOn ? '<span class="etiqueta" style="background:#b91c1c">PUENTE CRÍTICO</span>' : ""}
    <h2>Enlace ${e.id}</h2>
    <p class="nota">${esc(e.descripcion_tipo)}</p>`;
  const atributos = dl([
      ["Largo", fmt(e.longitud_km, 3) + " km"],
      ["Origen → destino", `<a href="#" data-v="${e.source}">v${e.source}</a> (grado ${e.grado_source}) → <a href="#" data-v="${e.target}">v${e.target}</a> (grado ${e.grado_target})`],
      ["Componente", esc(e.componente_nombre)],
      ["Capa de origen", `${esc(e.capa_origen)} · id ${esc(e.id_origen)}`],
      ["Tramo original", `id_red ${e.id_red}, pieza ${e.sub_id}`],
      ["Punto medio", `${fmt(e.lat, 4)}, ${fmt(e.lon, 4)}`],
    ]);
  const pie = `${e.sintetico ? `<div class="caja alerta"><b class="t">Justificación del enlace sintético</b>${esc(e.justificacion)}</div>` : ""}
    ${e.nodos_clave_extremos.length ? `<h3>Nodos clave en sus extremos</h3><ul class="lista">${e.nodos_clave_extremos.map((n) => `<li><span class="pt" style="background:${COL_ROL[n.rol]}"></span>${esc(n.nombre)}<small>${esc(n.tipo)}</small></li>`).join("")}</ul>` : ""}`;
  if (!E.sismoOn) {
    ficha(`${cabecera}
      <div class="caja"><b class="t">Probabilidad de falla en operación (2.0)</b><b style="font-size:22px">${fmt(e.prob_falla, 0)}</b>
        <small>Valor inicial de <code>red.prob_falla</code>. Activa el escenario Terremoto 8.8 para ver su probabilidad tras el sismo.</small></div>
      ${atributos}${pie}
      <p class="nota">Fuente: tabla <code>red</code> en PostGIS (topología de <code>pgr_createTopology</code>, 2.0).</p>`);
    $$("#ficha [data-v]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); abrirVertice(+a.dataset.v); });
    return;
  }
  ficha(`${cabecera}
    <div class="caja"><b class="t">Probabilidad de falla tras el M 8.8</b>
      <b style="font-size:22px">${fmt(e.prob_falla_8_8, 4)}</b> (${pct(e.prob_falla_8_8)})${barraP(e.prob_falla_8_8, escala(ESC_P, e.prob_falla_8_8))}
      <small>En operación: ${fmt(e.prob_falla, 0)} (apartado 2.0)</small></div>
    ${atributos}
    <h3>Cálculo del modelo (4.1 → 4.2)</h3>
    ${dl([
      ["Distancia al epicentro", fmt(e.dist_epicentro_km, 1) + " km"],
      ["Distancia a la ruptura", fmt(e.dist_ruptura_km, 1) + " km"],
      ["PGA (Youngs 1997)", fmt(e.pga_g, 3) + " g"],
      ["PGV = 91·PGA", fmt(e.pgv_cms, 1) + " cm/s"],
      e.codigo_tipo === "FERRY" ? ["Modelo", "HAZUS: falla de algún puerto"] : ["Tasa de cortes (ALA)", fmt(e.tasa_cortes_km, 4) + " /km"],
      e.k !== null ? ["Factor K", fmt(e.k, 1)] : ["Factor K", undefined],
    ])}
    <div class="caja fuente"><b class="t">Por qué este K / modelo (${esc(e.modelo)})</b>${esc(e.justificacion_k)}</div>
    ${e.codigo_tipo !== "FERRY" ? `<p class="nota">P = 1 − e<sup>−K·0,002416·PGV·L</sup> = 1 − e<sup>−${fmt(e.tasa_cortes_km, 4)}·${fmt(e.longitud_km, 2)}</sup></p>` : ""}
    ${c ? `<div class="caja alerta"><b class="t">Impacto en el ATTR (5.0)</b>
      Si se corta, separa los nodos clave en grupos de <b>${esc(c.separa)}</b> y el ATTR cae <b>${fmt(c.caida_attr_si_falla, 3)}</b>.<br>
      Pérdida esperada = ${fmt(c.prob_falla_8_8, 3)} × ${fmt(c.caida_attr_si_falla, 3)} = <b>${fmt(c.perdida_esperada_attr, 3)}</b><br><small>Cerca de: ${esc(c.lugar)}</small></div>` : ""}
    ${pie}
    <p class="nota">Fuente: tabla <code>red</code> en PostGIS (topología de <code>pgr_createTopology</code>, 2.0) y <code>sql/terremoto/02_modelo_falla.sql</code>.</p>`);
  $$("#ficha [data-v]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); abrirVertice(+a.dataset.v); });
}

async function abrirVertice(id) {
  E.ultima = () => abrirVertice(id);
  const v = await api(`/api/vertices/${id}`);
  capaSel.clearLayers();
  L.circleMarker([v.lat, v.lon], { pane: "sel", interactive: false, radius: 11, color: "#facc15", weight: 4, fill: false }).addTo(capaSel);
  ficha(`<span class="etiqueta" style="background:#475569">VÉRTICE pgRouting</span>
    <h2>Vértice ${v.id}</h2>
    ${dl([
      ["Grado (cnt)", v.grado + (v.grado === 1 ? " · extremo libre" : v.grado === 2 ? " · tránsito" : " · bifurcación")],
      ["chk / ein / eout", `${v.chk ?? "–"} / ${v.ein ?? "–"} / ${v.eout ?? "–"}`],
      ["Componente", esc(v.componente_nombre)],
      ["Coordenadas", `${fmt(v.lat, 5)}, ${fmt(v.lon, 5)}`],
      E.sismoOn ? ["Distancia a la ruptura", fmt(v.dist_ruptura_km, 1) + " km"] : ["", undefined],
      E.sismoOn ? ["PGA del 8.8", fmt(v.pga_g, 3) + " g"] : ["", undefined],
    ])}
    ${v.nodos_clave.length ? `<h3>Nodos clave asignados (${v.nodos_clave.length})</h3><ul class="lista">${v.nodos_clave.map((n) => `<li><span class="pt" style="background:${COL_ROL[n.rol]}"></span>${esc(n.nombre)}<small>${esc(n.tipo)}</small></li>`).join("")}</ul>` : ""}
    <h3>Enlaces que lo usan</h3>
    <ul class="lista">${v.enlaces.map((e) => `<li data-e="${e.id}"><span class="pt" style="background:${COL_TIPO[e.codigo_tipo]}"></span><div>Enlace ${e.id}<small>${NOM_TIPO[e.codigo_tipo]} · ${fmt(e.km, 2)} km</small></div><div class="der">p = ${E.sismoOn ? fmt(e.p, 3) : "0"}</div></li>`).join("")}</ul>
    <p class="nota">Fuente: <code>red_vertices_pgr</code> (creada por <code>pgr_createTopology</code>) y <code>vertice_sismo</code>.</p>`);
  $$("#ficha [data-e]").forEach((li) => li.onclick = () => abrirEnlace(+li.dataset.e));
}

// Varios nodos clave pueden caer en el mismo punto (p. ej. 3 DC de Santiago con la misma coordenada
// de PeeringDB): si hay más de uno a menos de 12 px del clic, se ofrece elegir.
function clicNodo(p) {
  const c = mapa.latLngToContainerPoint([p.lat, p.lon]);
  const cerca = E.nodos.features.map((f) => f.properties).filter((q) => nodoVisible(q)
    && mapa.latLngToContainerPoint([q.lat, q.lon]).distanceTo(c) < 12);
  if (cerca.length <= 1) return abrirNodo(p);
  capaSel.clearLayers();
  L.circleMarker([p.lat, p.lon], { pane: "sel", interactive: false, radius: 18, color: "#facc15", weight: 4, fill: false }).addTo(capaSel);
  ficha(`<h2>${cerca.length} nodos clave en este punto</h2>
    <p class="nota">Están en la misma ubicación o muy cerca a este zoom. Elige uno para ver su ficha.</p>
    <ul class="lista">${cerca.map((q) => `<li data-n="${q.id}"><span class="pt" style="background:${colorNodo(q)}"></span>
      <div>${FORMA[q.tipo] || ""} ${esc(q.nombre)}<small>${esc(q.tipo)} · ${q.rol}</small></div></li>`).join("")}</ul>`);
  $$("#ficha [data-n]").forEach((li) => li.onclick = () => abrirNodo(E.nodos.features[+li.dataset.n].properties));
}

function abrirNodo(p) {
  E.ultima = () => abrirNodo(p);
  capaSel.clearLayers();
  L.circleMarker([p.lat, p.lon], { pane: "sel", interactive: false, radius: 14, color: "#facc15", weight: 4, fill: false }).addTo(capaSel);
  const enSantiago = E.nodos.features.filter((f) => f.properties.id_vertice === p.id_vertice && f.properties.nombre !== p.nombre).map((f) => f.properties.nombre);
  ficha(`
    <span class="etiqueta" style="background:${COL_ROL[p.rol]}">${p.rol}</span>
    <span class="etiqueta" style="background:#475569">${esc(p.tipo)}</span>
    <h2>${FORMA[p.tipo] || ""} ${esc(p.nombre)}</h2>
    <p class="nota">${esc(p.detalle)}</p>
    <div class="caja"><b class="t">Por qué es nodo clave (criterio 1.0)</b>${esc(p.criterio)}</div>
    <div class="caja fuente"><b class="t">Fuente</b>${esc(p.fuente)}
      ${p.url_fuente ? `<br><a href="${esc(p.url_fuente)}" target="_blank" rel="noopener">Ver registro en la fuente ↗</a>`
        : `<br><small>Calculado sobre la propia red en <code>scripts/nodos_clave.py</code> (centralidad de intermediación${p.rank_intermediacion ? `, rank ${esc(p.rank_intermediacion)}` : ""}).</small>`}</div>
    <h3>Ubicación</h3>
    ${dl([
      ["Coordenadas", `${fmt(p.lat, 5)}, ${fmt(p.lon, 5)}`],
      p.desplaz_costa_km ? ["Coordenada de la fuente", `${fmt(p.lat_fuente, 5)}, ${fmt(p.lon_fuente, 5)}<br><small>movida ${fmt(p.desplaz_costa_km, 2)} km a la costa (OSM)</small>`] : ["", undefined],
      ["Lugar", esc(p.lugar)],
    ])}
    <h3>En la topología (2.0)</h3>
    ${dl([
      ["Vértice pgRouting", `<a href="#" data-v="${p.id_vertice}">v${p.id_vertice}</a>`],
      ["Distancia al vértice", fmt(p.dist_km, 2) + " km"],
      ["Grado del vértice", p.grado_vertice],
      ["Componente", esc(p.componente)],
      ["Nodo en el grafo Python (1.0)", esc(p.nodo_python)],
    ])}
    ${enSantiago.length ? `<p class="nota">Comparte vértice con: ${enSantiago.map(esc).join(", ")}.</p>` : ""}
    ${E.sismoOn ? `<h3>Terremoto y ATTR (4.1 → 5.0)</h3>
    ${dl([
      ["PGA en su vértice", fmt(p.pga_g, 3) + " g"],
      ["Distancia a la ruptura", fmt(p.dist_ruptura_km, 1) + " km"],
    ])}
    <div class="comparar">
      <div><span>Confiabilidad previa</span><b>${fmt(p.confiab_previa, 3)}</b></div>
      <div><span>Tras el 8.8</span><b style="color:${escala(ESC_PERD, p.perdida_pct ?? 0)}">${fmt(p.confiab_post, 3)}</b></div>
    </div>
    <p class="nota">Confiabilidad = promedio de R<sub>ij</sub> con los otros 54 nodos clave. Pérdida: <b>${p.perdida_pct !== null ? "−" + fmt(p.perdida_pct, 1) + " %" : "–"}</b>.</p>`
    : `<h3>ATTR en operación (3.1)</h3>
    <div class="comparar"><div><span>Confiabilidad en operación</span><b>${fmt(p.confiab_previa, 3)}</b></div></div>
    <p class="nota">Promedio de R<sub>ij</sub> con los otros 54 nodos clave, con <code>prob_falla</code> = 0. ${p.confiab_previa === 0
      ? "Vale 0 porque su vértice está en una componente aislada, sin ruta a la red principal."
      : "Vale 53/54 ≈ 0,98 y no 1 porque Grupo ZGH La Florida está aislado."} Activa Terremoto 8.8 para ver cuánto pierde con el sismo.</p>`}`);
  $$("#ficha [data-v]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); abrirVertice(+a.dataset.v); });
}

function abrirSismo() {
  E.ultima = null;
  const ev = E.sismo.evento;
  ficha(`<span class="etiqueta" style="background:#dc2626">USGS</span>
    <h2>${esc(ev.titulo)}</h2>
    ${dl([
      ["ID del evento", `<code>${esc(ev.id)}</code>`],
      ["Magnitud", `${ev.magnitud} ${esc(ev.tipo_magnitud)}`],
      ["Fecha (UTC)", esc(String(ev.fecha_utc).slice(0, 19).replace("T", " "))],
      ["Fecha (Chile, UTC−3)", esc(String(ev.fecha_chile).slice(0, 19).replace("T", " "))],
      ["Epicentro", `${ev.lat}, ${ev.lon}`],
      ["Profundidad", ev.profundidad_km + " km"],
      ["Estado", esc(ev.estado)],
      ["MMI máx. (ShakeMap)", ev.mmi_max],
      ["CDI máx.", ev.cdi_max],
      ["Reportes «lo sentí»", ev.reportes_sentido],
      ["Significancia", ev.significancia],
    ])}
    <div class="caja fuente"><b class="t">Fuente</b>API FDSN del USGS (GeoJSON), descargado por ID y por consulta (4.0).<br>
      <a href="https://earthquake.usgs.gov/earthquakes/eventpage/${esc(ev.id)}/executive" target="_blank" rel="noopener">Página del evento ↗</a> ·
      <a href="${esc(ev.url_geojson || `https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=${ev.id}&format=geojson`)}" target="_blank" rel="noopener">GeoJSON ↗</a></div>
    <div class="caja"><b class="t">Plano de ruptura (rectángulo)</b>530 × 180 km, rumbo 17,5°, manteo 18° (falla finita USGS / GEER 2010 §2.2). Las distancias del modelo se miden a este plano, no al punto del epicentro.</div>`);
}

function abrirEstacion(p) {
  E.ultima = null;
  ficha(`<span class="etiqueta" style="background:#7c3aed">ACELERÓGRAFO 27F</span><h2>${esc(p.estacion)}</h2>
    ${dl([["PGA medida", fmt(p.pga_obs_g, 2) + " g"], ["PGA del modelo", fmt(p.pga_modelo_g, 2) + " g"],
      ["Modelo / medida", fmt(p.pga_modelo_g / p.pga_obs_g, 2)], ["Distancia a la ruptura", fmt(p.dist_ruptura_km, 1) + " km"]])}
    <div class="caja fuente"><b class="t">Fuente</b>${esc(p.fuente)}</div>
    <p class="nota">Estos 12 registros calibran el término de evento δ<sub>27F</sub> de la ley de Youngs (1997).</p>`);
}

// ---------------------------------------------------------------------------
// Capas del terremoto
// ---------------------------------------------------------------------------
function dibujarSismo() {
  const s = E.sismo, ev = s.evento;
  L.geoJSON(s.ruptura, { renderer: rend, style: { color: "#dc2626", weight: 2, dashArray: "8 6", fillColor: "#dc2626", fillOpacity: 0.07 }, interactive: true })
    .bindTooltip("Plano de ruptura proyectado (530 × 180 km)").on("click", abrirSismo).addTo(capaEpi);
  L.marker([ev.lat, ev.lon], { icon: L.divIcon({ className: "mk-epi", html: "✸", iconSize: [26, 26], iconAnchor: [13, 13] }) })
    .bindTooltip(`<b>Epicentro M ${ev.magnitud}</b><br>${esc(ev.titulo)}`).on("click", abrirSismo).addTo(capaEpi);
  L.geoJSON(s.pga, { renderer: rend, style: (f) => ({ color: f.properties.color || "#f97316", weight: 2, opacity: 0.9 }),
    onEachFeature: (f, l) => l.bindTooltip(`PGA ShakeMap: ${f.properties.valor} ${f.properties.unidad === "pctg" ? "% g" : f.properties.unidad}`, { sticky: true }) }).addTo(capaPga);
  L.geoJSON(s.mmi, { renderer: rend, style: (f) => ({ color: f.properties.color || "#a855f7", weight: 2, opacity: 0.9 }),
    onEachFeature: (f, l) => l.bindTooltip(`MMI ShakeMap: ${f.properties.valor}`, { sticky: true }) }).addTo(capaMmi);
  L.geoJSON(s.estaciones, { pointToLayer: (f, ll) => L.circleMarker(ll, { renderer: rend, radius: 6, color: "#fff", weight: 2, fillColor: "#7c3aed", fillOpacity: 1 }),
    onEachFeature: (f, l) => { l.bindTooltip(`${esc(f.properties.estacion)}: ${f.properties.pga_obs_g} g medidos`); l.on("click", () => abrirEstacion(f.properties)); } }).addTo(capaEst);
}

// ---------------------------------------------------------------------------
// Paneles laterales: terremoto y resumen
// ---------------------------------------------------------------------------
function tabla(cols, filas) {
  return `<table><tr>${cols.map(([t, , n]) => `<th class="${n ? "n" : ""}">${t}</th>`).join("")}</tr>${filas.map((r) =>
    `<tr>${cols.map(([, f, n]) => `<td class="${n ? "n" : ""}">${f(r)}</td>`).join("")}</tr>`).join("")}</table>`;
}
function panelSismo() {
  const ev = E.sismo.evento;
  $("#s-evento").innerHTML = `<div class="caja" style="cursor:pointer" id="s-card"><b class="t">Sismo cargado desde USGS (4.0)</b>
    <b style="font-size:16px">${esc(ev.titulo)}</b><br>Mw ${ev.magnitud} · ${esc(String(ev.fecha_utc).slice(0, 19).replace("T", " "))} UTC · ${ev.profundidad_km} km<br>
    <small>Clic para ver la ficha completa y la fuente</small></div>`;
  $("#s-card").onclick = () => { mapa.setView([ev.lat, ev.lon], 7); abrirSismo(); };
  const m = E.modelo;
  $("#s-fragilidad").innerHTML = tabla([["Tipo", (r) => `<span style="color:${COL_TIPO[r.codigo_tipo]}">■</span> ${r.codigo_tipo}`], ["Modelo", (r) => r.modelo], ["K", (r) => r.k ?? "–", 1]], m.fragilidad);
  $("#s-param").innerHTML = tabla([["Parámetro", (r) => `<code>${esc(r.parametro)}</code>`], ["Valor", (r) => `${fmt(r.valor, r.valor % 1 ? 4 : 0)} ${esc(r.unidad || "")}`, 1], ["Fuente", (r) => `<small>${esc(r.fuente)}</small>`]], m.parametros);
  $("#s-est").innerHTML = tabla([["Estación", (r) => esc(r.properties.estacion)], ["Medida", (r) => fmt(r.properties.pga_obs_g, 2), 1], ["Modelo", (r) => fmt(r.properties.pga_modelo_g, 2), 1]], E.sismo.estaciones.features);
  $("#s-desc").innerHTML = E.sismo.descargas.length ? tabla([["Archivo", (r) => `<small>${esc(r.archivo || r.nombre || Object.values(r)[0])}</small>`], ["SHA-256", (r) => `<small><code>${esc(String(r.sha256 || "").slice(0, 12))}…</code></small>`]], E.sismo.descargas) : "<p class='nota'>Sin registro.</p>";
}
function panelResumen() {
  const R = E.resumen, ap = Object.fromEntries(R.attr.map((a) => [a.escenario, a]));
  const pre = ap.operacion?.attr, post = ap.post_terremoto_8_8?.attr;
  $("#r-attr").innerHTML = `<h3>ATTR de los 55 nodos clave (1.485 pares)</h3>
    <div class="comparar"><div><span>En operación (3.1)</span><b>${fmt(pre, 4)}</b></div>
      <div><span>Tras el M 8.8 (5.0)</span><b style="color:var(--peligro)">${fmt(post, 4)}</b></div></div>
    <p class="nota">Caída de <b>${fmt(pre - post, 3)}</b> (<b>−${fmt((1 - post / pre) * 100, 1)} %</b>). Post 8.8 con Monte Carlo, N = ${ap.post_terremoto_8_8?.iteraciones ?? "–"},
      IC 95 % [${fmt(ap.post_terremoto_8_8?.ic95_inf, 4)}, ${fmt(ap.post_terremoto_8_8?.ic95_sup, 4)}].</p>
    ${tabla([["Escenario", (r) => `<small>${esc(r.escenario)}</small>`], ["Método", (r) => r.metodo], ["ATTR", (r) => fmt(r.attr, 4), 1]], R.attr)}`;
  $("#r-tipos").innerHTML = tabla([["Tipo", (r) => `<span style="color:${COL_TIPO[r.tipo]}">■</span> ${r.tipo}`], ["Enl.", (r) => r.enlaces, 1], ["km", (r) => fmt(r.km, 0), 1], ["Cortes esp.", (r) => fmt(r.cortes_esperados, 1), 1]], R.tipos);
  $("#r-comp").innerHTML = tabla([["Comp.", (r) => r.nombre], ["Vért.", (r) => r.vertices, 1], ["km", (r) => fmt(r.km, 1), 1], ["Latitud", (r) => `<small>${fmt(r.lat_norte, 1)} a ${fmt(r.lat_sur, 1)}</small>`]], R.componentes);
  $("#r-criticos").innerHTML = E.criticos.map((c) => `<li data-e="${c.id}"><span class="pt" style="background:${COL_TIPO[c.codigo_tipo]}"></span>
    <div>Enlace ${c.id} · ${NOM_TIPO[c.codigo_tipo]}<small>${esc(c.lugar)} · separa ${esc(c.separa)}</small></div>
    <div class="der"><b>${fmt(c.perdida_esperada_attr, 3)}</b><small>p ${fmt(c.prob_falla_8_8, 2)}</small></div></li>`).join("");
  $("#r-sint").innerHTML = E.sint.map((s) => `<li data-e="${s.id}"><span class="pt" style="background:#db2777"></span>
    <div>${esc(s.regla)} · ${esc(s.comp_desde)} ↔ ${esc(s.comp_hasta)}<small>${esc(s.justificacion)}</small></div><div class="der">${fmt(s.dist_m, 1)} m</div></li>`).join("");
  $$("#r-criticos li, #r-sint li").forEach((li) => li.onclick = () => irAEnlace(+li.dataset.e));
  kpis();
}
function kpis() {
  const R = E.resumen, t = R.totales, ap = Object.fromEntries(R.attr.map((a) => [a.escenario, a]));
  const pre = ap.operacion?.attr, post = ap.post_terremoto_8_8?.attr;
  $("#kpis").innerHTML = `<div class="kpi"><b>${t.enlaces.toLocaleString("es-CL")}</b><span>enlaces · ${fmt(t.km, 0)} km</span></div>
    <div class="kpi"><b>${t.nodos_clave}</b><span>nodos clave</span></div>
    <div class="kpi"><b>${fmt(pre, 3)}</b><span>ATTR en operación</span></div>`
    + (E.sismoOn ? `<div class="kpi malo"><b>${fmt(post, 3)}</b><span>ATTR tras el 8.8 (−${fmt((1 - post / pre) * 100, 0)} %)</span></div>
    <div class="kpi malo"><b>${fmt(t.cortes_esperados, 1)}</b><span>cortes esperados</span></div>` : "");
}

// ---------------------------------------------------------------------------
// Escenario: red en operación ⇄ terremoto 8.8
// ---------------------------------------------------------------------------
const CAPAS_SISMO = [["#c-epi", capaEpi], ["#c-pga", capaPga], ["#c-mmi", capaMmi], ["#c-est", capaEst]];
function aplicarEscenario(on) {
  E.sismoOn = on;
  try { localStorage.setItem("escenario", on ? "terremoto" : "operacion"); } catch { /* sin almacenamiento */ }
  document.body.classList.toggle("sin-sismo", !on);
  $("#esc-op").classList.toggle("activo", !on);
  $("#esc-sismo").classList.toggle("activo", on);
  $("#esc-op").setAttribute("aria-pressed", String(!on));
  $("#esc-sismo").setAttribute("aria-pressed", String(on));

  // Capas del terremoto: se ocultan sin perder lo que el usuario tenía marcado
  CAPAS_SISMO.forEach(([id, capa]) => {
    $(id).disabled = !on;
    on && $(id).checked ? capa.addTo(mapa) : mapa.removeLayer(capa);
  });
  $$("#f-sismo input").forEach((i) => i.disabled = !on);

  // Modos de color que dependen del sismo: se desactivan y se recuerda el último para volver a él
  $$("#modo-color button").forEach((b) => b.disabled = !on && MODOS_SISMO.includes(b.dataset.modo));
  $('#n-modo button[data-modo="perdida"]').disabled = !on;
  if (!on) {
    if (MODOS_SISMO.includes(E.modo)) { E.modoSismo = E.modo; E.modo = "tipo"; }
    if (E.nModo === "perdida") { E.nModoSismo = true; E.nModo = "rol"; }
  } else {
    if (E.modoSismo) { E.modo = E.modoSismo; E.modoSismo = null; }
    if (E.nModoSismo) { E.nModo = "perdida"; E.nModoSismo = false; }
  }
  $$("#modo-color button").forEach((b) => b.classList.toggle("activo", b.dataset.modo === E.modo));
  $$("#n-modo button").forEach((b) => b.classList.toggle("activo", b.dataset.modo === E.nModo));

  dibujarRed(); dibujarNodos(); kpis(); ordenarCapas();
  // La ficha abierta se rehace con el escenario nuevo (la del sismo o una estación se cierra al apagarlo)
  if (!$("#ficha").hidden) {
    if (E.ultima) E.ultima();
    else if (!on) $("#ficha-cerrar").click();
  }
}
function irAEnlace(id) {
  const f = E.red.features.find((x) => x.properties.id === id);
  if (f) mapa.fitBounds(L.geoJSON(f).getBounds(), { maxZoom: 12, padding: [60, 60] });
  abrirEnlace(id);
}

// ---------------------------------------------------------------------------
// Controles
// ---------------------------------------------------------------------------
function chips(cont, items, set, colores, onChange) {
  cont.innerHTML = items.map(([k, t, n]) => `<label class="chip ${set.has(k) ? "" : "off"}"><input type="checkbox" value="${esc(k)}" ${set.has(k) ? "checked" : ""}>
    <span class="pt" style="background:${colores(k)}"></span>${esc(t)}${n !== undefined ? ` <small>${n}</small>` : ""}</label>`).join("");
  cont.querySelectorAll("input").forEach((i) => i.onchange = () => {
    i.checked ? set.add(i.value) : set.delete(i.value);
    i.parentElement.classList.toggle("off", !i.checked); onChange();
  });
}
function segmentado(cont, onChange) {
  cont.querySelectorAll("button").forEach((b) => b.onclick = () => {
    cont.querySelectorAll("button").forEach((x) => x.classList.toggle("activo", x === b)); onChange(b.dataset.modo);
  });
}
function controles() {
  // Pestañas
  $$(".pestana").forEach((b) => b.onclick = () => {
    $$(".pestana").forEach((x) => x.classList.toggle("activa", x === b));
    $$(".contenido").forEach((c) => c.classList.toggle("activa", c.id === "tab-" + b.dataset.tab));
  });
  $("#btn-panel").onclick = () => $("#panel").classList.toggle("abierto");

  // Red
  segmentado($("#modo-color"), (m) => { E.modo = m; E.modoSismo = null; capaRed.setStyle(estiloEnlace); leyendaRed(); });
  $("#esc-op").onclick = () => E.sismoOn && aplicarEscenario(false);
  $("#esc-sismo").onclick = () => !E.sismoOn && aplicarEscenario(true);
  $("#s-activar").onclick = () => aplicarEscenario(true);
  const cuenta = {}; E.red.features.forEach((f) => cuenta[f.properties.tipo] = (cuenta[f.properties.tipo] || 0) + 1);
  chips($("#f-tipos"), Object.keys(COL_TIPO).map((t) => [t, NOM_TIPO[t], cuenta[t] || 0]), E.f.tipos, (k) => COL_TIPO[k], dibujarRed);
  $("#f-comp").innerHTML = `<option value="">Todas</option><option value="C1">Solo C1 (principal)</option><option value="otras">Solo componentes aisladas</option>`
    + E.resumen.componentes.slice(1).map((c) => `<option value="${c.nombre}">${c.nombre} · ${fmt(c.km, 1)} km</option>`).join("");
  $("#f-comp").onchange = (e) => { E.f.comp = e.target.value; dibujarRed(); };
  const rangos = () => {
    let a = +$("#f-pmin").value, b = +$("#f-pmax").value;
    if (a > b) [a, b] = [b, a];
    Object.assign(E.f, { pmin: a, pmax: b, pga: +$("#f-pga").value, km: +$("#f-km").value });
    $("#f-p-txt").textContent = `${fmt(a, 2)} – ${fmt(b, 2)}`;
    $("#f-pga-txt").textContent = fmt(E.f.pga, 2) + " g";
    $("#f-km-txt").textContent = fmt(E.f.km, 1) + " km";
    dibujarRed();
  };
  ["#f-pmin", "#f-pmax", "#f-pga", "#f-km"].forEach((s) => $(s).oninput = rangos);
  $("#f-sint").onchange = (e) => { E.f.sint = e.target.checked; dibujarRed(); };
  $("#f-puente").onchange = (e) => { E.f.puente = e.target.checked; dibujarRed(); };
  $("#f-reset").onclick = () => {
    E.f.tipos = new Set(Object.keys(COL_TIPO));
    $("#f-pmin").value = 0; $("#f-pmax").value = 1; $("#f-pga").value = 0; $("#f-km").value = 0;
    $("#f-sint").checked = $("#f-puente").checked = false; E.f.sint = E.f.puente = false;
    $("#f-comp").value = ""; E.f.comp = "";
    chips($("#f-tipos"), Object.keys(COL_TIPO).map((t) => [t, NOM_TIPO[t], cuenta[t] || 0]), E.f.tipos, (k) => COL_TIPO[k], dibujarRed);
    rangos();
  };

  // Capas
  const alternar = (id, capa, despues) => $(id).onchange = (e) => { e.target.checked ? capa.addTo(mapa) : mapa.removeLayer(capa); despues && despues(); ordenarCapas(); };
  alternar("#c-red", capaRed);
  alternar("#c-vert", capaVert, () => dibujarVertices());
  alternar("#c-nodos", capaNodos, () => { if (!$("#c-nodos").checked) mapa.removeLayer(capaLineas); else if (E.n.lineas) capaLineas.addTo(mapa); });
  alternar("#c-epi", capaEpi); alternar("#c-pga", capaPga); alternar("#c-mmi", capaMmi); alternar("#c-est", capaEst);
  $$("#f-grados input").forEach((i) => i.onchange = () => {
    E.grados = new Set($$("#f-grados input:checked").map((x) => +x.value)); dibujarVertices();
  });

  // Nodos
  const tipos = [...new Set(E.nodos.features.map((f) => f.properties.tipo))];
  E.n.tipos = new Set(tipos);
  const cRol = {}, cTipo = {};
  E.nodos.features.forEach((f) => { cRol[f.properties.rol] = (cRol[f.properties.rol] || 0) + 1; cTipo[f.properties.tipo] = (cTipo[f.properties.tipo] || 0) + 1; });
  chips($("#n-roles"), Object.keys(COL_ROL).map((r) => [r, r, cRol[r] || 0]), E.n.roles, (k) => COL_ROL[k], dibujarNodos);
  chips($("#n-tipos"), tipos.map((t) => [t, `${FORMA[t] || ""} ${t}`, cTipo[t]]), E.n.tipos, () => "#64748b", dibujarNodos);
  $("#n-buscar").oninput = (e) => { E.n.q = e.target.value; dibujarNodos(); };
  $("#n-lineas").onchange = (e) => { E.n.lineas = e.target.checked; dibujarNodos(); };
  segmentado($("#n-modo"), (m) => { E.nModo = m; E.nModoSismo = false; dibujarNodos(); });
}
function dibujarVertices() {
  capaVert.clearLayers();
  if (!$("#c-vert").checked) return;
  capaVert.addData(E.vert.features.filter((f) => E.grados.has(Math.min(f.properties.grado, 3))));
  ordenarCapas();
}

// ---------------------------------------------------------------------------
// Inicio
// ---------------------------------------------------------------------------
(async function inicio() {
  try {
    const [red, vert, nodos, sismo, resumen, modelo, criticos, sint] = await Promise.all([
      api("/api/red"), api("/api/vertices"), api("/api/nodos_clave"), api("/api/terremoto"),
      api("/api/resumen"), api("/api/modelo"), api("/api/criticos?limite=25"), api("/api/sinteticos")]);
    Object.assign(E, { red, vert, nodos, sismo, resumen, modelo, criticos, sint });
    E.compOrden = resumen.componentes.map((c) => c.componente);
    E.compNombres = Object.fromEntries(resumen.componentes.map((c) => [c.componente, c.nombre]));
    controles();
    dibujarSismo(); panelSismo(); panelResumen();
    aplicarEscenario(E.sismoOn);   // dibuja red y nodos según el escenario guardado
    // Encuadre en Chile continental (los nodos clave; la red tiene algún tramo lejano en el Pacífico)
    mapa.fitBounds(L.geoJSON(nodos).getBounds(), { padding: [20, 20] });
    // Enlaces directos: ?color=p|pga|crit|comp|tipo  &vista=lat,lon,zoom
    const q = new URLSearchParams(location.search);
    if (q.get("color")) $(`#modo-color button[data-modo="${q.get("color")}"]`)?.click();
    const vista = (q.get("vista") || "").split(",").map(Number);
    if (vista.length === 3 && vista.every(Number.isFinite)) mapa.setView([vista[0], vista[1]], vista[2]);
    $("#cargando").hidden = true;
  } catch (err) {
    console.error(err);
    $("#cargando").innerHTML = `<div style="max-width:440px;text-align:center">No se pudieron cargar los datos.<br><small>${esc(err.message)}</small><br><br>
      <small>¿Está arriba la base? <code>docker compose up -d db</code> y luego correr los scripts del 2.0 al 5.0.</small></div>`;
  }
})();
