/* WitnessWoods UI. Every number shown comes from web/data/*.json; nothing is hard-coded. */
"use strict";

const COL = { HIGH: "#E0473C", MEDIUM: "#F0A44B", LOW: "#8FBF9A", UNDER_SURVEYED: "#8C9AA0", ALREADY_FELLED: "#6B5444" };
const GREY = "#56635c", SNOW = "#E8EFE9", MOSS = "#CFE3C8", BARK = "#6B5444";
const LABEL = { HIGH: "HIGH", MEDIUM: "MEDIUM", LOW: "LOW", UNDER_SURVEYED: "UNDER-SURVEYED", ALREADY_FELLED: "ALREADY FELLED" };
// Tiles go through the local server, which caches them to disk (see server.py /tiles)
const EOX = location.origin + "/tiles/eox/{z}/{x}/{y}";
const OSM = location.origin + "/tiles/osm/{z}/{x}/{y}";
const params = new URLSearchParams(location.hash.slice(1));

const S = {
  week: null, stats: null, ev: null, condense: null, replay: null, map: null, mapReady: false,
  sites: [], byId: {}, cache: {}, current: null, ringMarkers: [], fadeTimers: {},
  playing: false, step: 0, timer: null, lastSite: null, finished: false, liveBusy: false,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fileId = (b) => b.replace(/ /g, "_");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDate = (iso, year = true) => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS[m - 1]}${year ? " " + y : ""}`; };
const pct = (x) => `${Math.round(x * 100)}%`;
async function getJSON(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); }

/* ------------------------------------------------------------------ boot */
async function boot() {
  bindUI();
  const [week, stats] = await Promise.all([getJSON("data/week.json"), getJSON("data/stats.json")]);
  S.week = week; S.stats = stats;
  S.sites = week.sites; S.sites.forEach((s) => (S.byId[s.beteckn] = s));
  renderCounters(); renderHeadline(); renderIntro();
  initMap();
  Promise.all([getJSON("data/replay.json"), getJSON("data/evidence_test.json"), getJSON("data/condense.json")])
    .then(([replay, ev, condense]) => { S.replay = replay; S.ev = ev; S.condense = condense; renderEvidence(); renderCondense(); applyHash(); })
    .catch((e) => console.warn("secondary data:", e.message));
}

function renderCounters() {
  const w = S.week, c = w.counts;
  const part = (cls, n, label) => `<span class="${cls}"><b>${n ?? 0}</b> ${label}</span>`;
  const extra = w.investigated < w.notified ? `<span class="dot">·</span>${part("", w.investigated, "investigated")}` : "";
  $("feed-scope").textContent = `${w.county} · ${fmtDate(w.received_from, false)}–${fmtDate(w.received_to, false)}`;
  $("counters").innerHTML = [
    part("", w.notified, "notified this week"), extra,
    `<span class="dot">·</span>`, part("c-high", c.HIGH, "HIGH"), `<span class="dot">·</span>`, part("c-medium", c.MEDIUM, "MEDIUM"),
    `<span class="dot">·</span>`, part("c-under", c.UNDER_SURVEYED, "under-surveyed"),
  ].join(" ");
}

function renderIntro() {
  const highs = S.sites.filter((s) => s.priority === "HIGH");
  $("feed-intro").innerHTML = `
    <div class="intro-h">How it works</div>
    <ol class="steps">
      <li>Reads each new felling notification and checks whether the site is already felled.</li>
      <li>Cross-examines species records (Artportalen via GBIF), the Swedish Red List 2025 and how much anyone has recorded nearby.</li>
      <li>Writes a dossier that cites its evidence and sets a priority for human review, or says the site is under-surveyed.</li>
    </ol>
    <div class="intro-h">This week's HIGH sites (${highs.length})</div>
    <ul class="high-list">${highs.map((s) => `<li><button class="high-item" data-b="${esc(s.beteckn)}"><span class="mono">${esc(s.beteckn)}</span> · ${esc(titleCase(s.kommun))}<span class="hl">${esc(s.headline)}</span></button></li>`).join("")}</ul>`;
  document.querySelectorAll(".high-item").forEach((el) => (el.onclick = () => { showTab("map"); selectSite(el.dataset.b); }));
}

function renderHeadline() {
  const st = S.stats;
  if (!st || !st.headline) return;
  const sub = "Same measure over six weeks: " + st.zero_record_share.map((z) => `${z.county} ${pct(z.share_no_record_within_250m)}`).join(" · ");
  const wins = [...new Set(st.zero_record_share.map((z) => z.window.join()))];
  const win = st.zero_record_share[0].window;
  const when = wins.length === 1 ? `received ${fmtDate(win[0], false)}–${fmtDate(addDays(win[1], -1))}` : "in each county's six-week window";
  $("headline").innerHTML = `${esc(st.headline)}<span class="sub">${esc(sub)} of notified regeneration-felling sites, ${when}</span>`;
}
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

/* ------------------------------------------------------------------ map */
function initMap() {
  const tilesOff = params.get("tiles") === "off"; // test hook: an unreachable tile host exercises the real fallback path
  const bad = "http://127.0.0.1:9/{z}/{x}/{y}.png";
  const map = new maplibregl.Map({
    container: "map", attributionControl: { compact: false }, pitch: 30, maxPitch: 60, fadeDuration: 150,
    style: {
      version: 8,
      sources: {
        eox: { type: "raster", tiles: [tilesOff ? bad : EOX], tileSize: 256, maxzoom: 15,
               attribution: '<a href="https://s2maps.eu" target="_blank" rel="noopener">Sentinel-2 cloudless</a> by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2023), CC BY-NC-SA 4.0' },
        osm: { type: "raster", tiles: [tilesOff ? bad : OSM], tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors" },
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#0D1411" } },
        { id: "eox", type: "raster", source: "eox", paint: { "raster-brightness-max": 0.88, "raster-saturation": -0.05 } },
        { id: "osm", type: "raster", source: "osm", layout: { visibility: "none" }, paint: { "raster-brightness-max": 0.6, "raster-saturation": -0.6 } },
      ],
    },
    bounds: bbox(S.sites.map((s) => s.centroid)), fitBoundsOptions: { padding: pad() },
  });
  S.map = map;
  watchTiles(map);
  map.on("load", () => {
    addImages(map);
    addLayers(map);
    S.mapReady = true;
    applyHash();
  });
}

function pad(extraRight = 0) {
  const feedW = document.querySelector(".feed").getBoundingClientRect().width;
  return { top: 110, bottom: 40, left: feedW + 50, right: 40 + extraRight };
}
function bbox(pts) {
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
}

function watchTiles(map) {
  const st = { eox: { ok: 0, err: 0 }, osm: { ok: 0, err: 0 }, mode: "eox" };
  map.on("error", (e) => { if (e.sourceId && st[e.sourceId]) { st[e.sourceId].err++; check(); } });
  map.on("sourcedata", (e) => { if (e.tile && st[e.sourceId] && e.tile.state === "loaded") st[e.sourceId].ok++; });
  const check = () => {
    if (st.mode === "eox" && st.eox.ok === 0 && st.eox.err >= 4) switchTo("osm");
    else if (st.mode === "osm" && st.osm.ok === 0 && st.osm.err >= 4) switchTo("none");
  };
  const switchTo = (mode) => {
    st.mode = mode;
    if (!map.getLayer("eox")) return;
    map.setLayoutProperty("eox", "visibility", "none");
    map.setLayoutProperty("osm", "visibility", mode === "osm" ? "visible" : "none");
    if (mode === "none") { $("tile-note").hidden = false; if (map.getLayer("county")) map.setPaintProperty("county", "line-opacity", 0.9); }
  };
  setTimeout(() => { if (st.mode === "eox" && st.eox.ok === 0) switchTo("osm"); }, 6000);
  setTimeout(() => { if (st.mode === "osm" && st.osm.ok === 0) switchTo("none"); }, 12000);
}

function canvasImage(w, h, draw) {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d"); draw(g);
  return g.getImageData(0, 0, w, h);
}
function addImages(map) {
  map.addImage("hatch", canvasImage(16, 16, (g) => {
    g.strokeStyle = "rgba(140,154,160,0.95)"; g.lineWidth = 2.2;
    for (let i = -16; i <= 32; i += 8) { g.beginPath(); g.moveTo(i, 16); g.lineTo(i + 16, 0); g.stroke(); }
  }), { pixelRatio: 2 });
  for (const [name, col] of [["tri-elig", COL.HIGH], ["tri-other", COL.MEDIUM]]) {
    map.addImage(name, canvasImage(36, 36, (g) => {
      g.strokeStyle = col; g.lineWidth = 4.5; g.lineJoin = "round";
      g.beginPath(); g.moveTo(18, 5); g.lineTo(32, 30); g.lineTo(4, 30); g.closePath(); g.stroke();
    }), { pixelRatio: 2 });
  }
}

const FADE = ["coalesce", ["feature-state", "fade"], 1];
const SEL = ["boolean", ["feature-state", "selected"], false];

function addLayers(map) {
  const polys = { type: "FeatureCollection", features: S.sites.map((s) => ({ type: "Feature", geometry: s.polygon, properties: { beteckn: s.beteckn, priority: s.priority } })) };
  const points = { type: "FeatureCollection", features: S.sites.map((s) => ({ type: "Feature", geometry: { type: "Point", coordinates: s.centroid }, properties: { beteckn: s.beteckn, priority: s.priority } })) };
  map.addSource("county", { type: "geojson", data: "data/county.json" });
  map.addSource("sites", { type: "geojson", data: polys, promoteId: "beteckn" });
  map.addSource("dots", { type: "geojson", data: points, promoteId: "beteckn" });
  map.addSource("fellings", { type: "geojson", data: empty() });
  map.addSource("rings", { type: "geojson", data: empty() });
  map.addSource("species", { type: "geojson", data: empty() });
  map.addSource("halos", { type: "geojson", data: empty() });
  const color = ["match", ["get", "priority"], "HIGH", COL.HIGH, "MEDIUM", COL.MEDIUM, "LOW", COL.LOW, "UNDER_SURVEYED", COL.UNDER_SURVEYED, COL.ALREADY_FELLED];
  const zoomOut = (v) => ["interpolate", ["linear"], ["zoom"], 10.5, v, 12.5, 0];

  map.addLayer({ id: "county", type: "line", source: "county", paint: { "line-color": "#9AABA1", "line-width": 1.2, "line-opacity": 0.35, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "fellings-fill", type: "fill", source: "fellings", paint: { "fill-color": BARK, "fill-opacity": 0.32 } });
  map.addLayer({ id: "fellings-line", type: "line", source: "fellings", paint: { "line-color": "#a3826b", "line-width": 1.2 } });
  // grey base, crossfaded to colour when a site's verdict lands (feature-state "fade" 0 -> 1)
  map.addLayer({ id: "sites-grey", type: "fill", source: "sites", paint: { "fill-color": GREY, "fill-opacity": ["*", 0.3, ["-", 1, FADE]] } });
  map.addLayer({ id: "sites-fill", type: "fill", source: "sites", filter: ["in", ["get", "priority"], ["literal", ["HIGH", "MEDIUM", "LOW"]]],
                 paint: { "fill-color": color, "fill-opacity": ["*", 0.35, FADE] } });
  map.addLayer({ id: "sites-hatch", type: "fill", source: "sites", filter: ["==", ["get", "priority"], "UNDER_SURVEYED"],
                 paint: { "fill-pattern": "hatch", "fill-opacity": ["*", 0.9, FADE] } });
  map.addLayer({ id: "sites-glow", type: "line", source: "sites", filter: ["==", ["get", "priority"], "HIGH"],
                 paint: { "line-color": COL.HIGH, "line-width": 9, "line-blur": 7, "line-opacity": ["*", 0.75, FADE] } });
  map.addLayer({ id: "sites-line-grey", type: "line", source: "sites", paint: { "line-color": GREY, "line-width": 1.4, "line-opacity": ["-", 1, FADE] } });
  map.addLayer({ id: "sites-line", type: "line", source: "sites", filter: ["!=", ["get", "priority"], "ALREADY_FELLED"],
                 paint: { "line-color": color, "line-width": 1.7, "line-opacity": FADE } });
  map.addLayer({ id: "sites-felled", type: "line", source: "sites", filter: ["==", ["get", "priority"], "ALREADY_FELLED"],
                 paint: { "line-color": "#a3826b", "line-width": 2, "line-dasharray": [2, 1.5], "line-opacity": FADE } });
  map.addLayer({ id: "sites-selected", type: "line", source: "sites", paint: { "line-color": MOSS, "line-width": 3, "line-opacity": ["case", SEL, 1, 0] } });
  // centroid dots so 1-5 ha sites read at county zoom; they fade out as the real polygons become visible
  map.addLayer({ id: "dots-glow", type: "circle", source: "dots", filter: ["==", ["get", "priority"], "HIGH"],
                 paint: { "circle-color": COL.HIGH, "circle-radius": 13, "circle-blur": 1, "circle-opacity": zoomOut(["*", 0.7, FADE]) } });
  map.addLayer({ id: "dots", type: "circle", source: "dots",
                 layout: { "circle-sort-key": ["match", ["get", "priority"], "HIGH", 4, "MEDIUM", 3, "LOW", 2, 1] },
                 paint: { "circle-color": ["case", ["<", FADE, 0.5], GREY, color], "circle-radius": ["match", ["get", "priority"], "HIGH", 6.5, "MEDIUM", 5.5, 4.5],
                          "circle-opacity": zoomOut(["case", ["==", ["get", "priority"], "UNDER_SURVEYED"], 0.35, ["==", ["get", "priority"], "ALREADY_FELLED"], 0, 0.95]),
                          "circle-stroke-color": ["case", ["<", FADE, 0.5], GREY, color], "circle-stroke-width": 1.5,
                          "circle-stroke-opacity": zoomOut(1) } });
  map.addLayer({ id: "rings", type: "line", source: "rings", paint: { "line-color": SNOW, "line-width": 1, "line-opacity": 0.75, "line-dasharray": [3, 3] } });
  const pcol = ["case", ["get", "eligible"], COL.HIGH, COL.MEDIUM];
  map.addLayer({ id: "species-halo", type: "circle", source: "halos",
                 paint: { "circle-color": pcol, "circle-opacity": 0.035, "circle-stroke-color": pcol, "circle-stroke-opacity": 0.3, "circle-stroke-width": 0.8,
                          "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 0, ["/", ["get", "h22"], 4194304], 22, ["get", "h22"]] } });
  map.addLayer({ id: "species-felled", type: "circle", source: "species", filter: ["has", "felled_after"],
                 paint: { "circle-radius": 10, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#b08a6e", "circle-stroke-width": 2.5 } });
  map.addLayer({ id: "species-site", type: "circle", source: "species", filter: ["!=", ["get", "mobility"], "mobile"],
                 paint: { "circle-color": pcol, "circle-radius": 4.5, "circle-stroke-color": "#0D1411", "circle-stroke-width": 1.2 } });
  map.addLayer({ id: "species-mobile", type: "symbol", source: "species", filter: ["==", ["get", "mobility"], "mobile"],
                 layout: { "icon-image": ["case", ["get", "eligible"], "tri-elig", "tri-other"], "icon-size": 0.85, "icon-allow-overlap": true, "icon-ignore-placement": true } });

  for (const id of ["sites-grey", "sites-fill", "sites-hatch", "dots"]) {
    map.on("click", id, (e) => { if (!S.playing) selectSite(e.features[0].properties.beteckn); });
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
  for (const id of ["species-site", "species-mobile"]) {
    map.on("click", id, (e) => speciesPopup(e.features[0]));
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
}
const empty = () => ({ type: "FeatureCollection", features: [] });

function speciesPopup(f) {
  const p = f.properties;
  new maplibregl.Popup({ offset: 10, maxWidth: "280px" }).setLngLat(f.geometry.coordinates).setHTML(
    `<b>${esc(p.name)}</b> <span class="muted">(${esc(p.swedish)})</span><br>
     Red List ${esc(p.category)} · ${p.mobility === "mobile" ? "mobile" : p.mobility === "site_bound" ? "site-bound" : "other"}${p.eligible === true || p.eligible === "true" ? " · eligible" : ""}<br>
     Recorded ${esc(p.year)} · ${esc(p.dist_m)} m from the site · ±${Math.round(p.unc_m)} m<br>
     <a href="${esc(p.url)}" target="_blank" rel="noopener">GBIF record ${esc(p.key)}</a>`).addTo(S.map);
}

function setFade(b, v) {
  for (const src of ["sites", "dots"]) S.map.setFeatureState({ source: src, id: b }, { fade: v });
}
function animateFade(b, ms = 700) {
  const t0 = performance.now();
  cancelAnimationFrame(S.fadeTimers[b]);
  const f = (t) => { const k = Math.min(1, (t - t0) / ms); setFade(b, k); if (k < 1) S.fadeTimers[b] = requestAnimationFrame(f); };
  S.fadeTimers[b] = requestAnimationFrame(f);
}
function setSelected(b) {
  if (S.current) S.map.setFeatureState({ source: "sites", id: S.current }, { selected: false });
  if (b) S.map.setFeatureState({ source: "sites", id: b }, { selected: true });
}

/* ------------------------------------------------------------------ site selection + dossier */
async function loadSite(b, fresh = false) {
  if (!S.cache[b] || fresh) S.cache[b] = await getJSON(`data/sites/${fileId(b)}.json`);
  return S.cache[b];
}

async function selectSite(b, { fly = true } = {}) {
  if (!S.mapReady || !S.byId[b]) return;
  const d = await loadSite(b);
  setSelected(b); S.current = b;
  document.body.classList.add("dossier-open");
  $("dossier").setAttribute("aria-hidden", "false");
  renderDossier(d);
  history.replaceState(null, "", `#site=${fileId(b)}`);
  const map = S.map;
  map.getSource("fellings").setData(d.geometry.fellings);
  const feats = d.geometry.species.features.map((f) => {
    const lat = f.geometry.coordinates[1];
    const mpp22 = (78271.517 * Math.cos((lat * Math.PI) / 180)) / 4194304;
    const props = { ...f.properties, h22: Math.max(f.properties.unc_m, 1) / mpp22 };
    if (props.felled_after == null) delete props.felled_after;
    return { ...f, properties: props };
  });
  map.getSource("species").setData({ type: "FeatureCollection", features: feats });
  // one halo per distinct (position, uncertainty): many coarse records share a single point
  const seen = new Map();
  for (const f of feats) {
    const key = `${f.geometry.coordinates.join(",")}|${f.properties.unc_m}`;
    const prev = seen.get(key);
    if (!prev || (f.properties.eligible && !prev.properties.eligible)) seen.set(key, f);
  }
  map.getSource("halos").setData({ type: "FeatureCollection", features: [...seen.values()] });
  drawRings(d.geometry.rings);
  if (fly) {
    const outer = d.geometry.rings[d.geometry.rings.length - 1].geometry;
    const coords = outer.type === "Polygon" ? outer.coordinates[0] : outer.coordinates.flat(2);
    const box = bbox(coords.length ? coords : [S.byId[b].centroid]);
    map.fitBounds(box, { padding: pad(document.querySelector(".dossier").getBoundingClientRect().width + 20), pitch: 38, duration: 1600, maxZoom: 15.5 });
  }
}

function drawRings(rings) {
  const map = S.map;
  S.ringMarkers.forEach((m) => m.remove()); S.ringMarkers = [];
  clearTimeout(S.ringTimer);
  const lines = rings.filter((r) => r.m > 0).map((r) => ({ m: r.m, f: { type: "Feature", geometry: toLines(r.geometry), properties: { m: r.m } }, top: topPoint(r.geometry) }));
  let i = 0;
  const next = () => {
    map.getSource("rings").setData({ type: "FeatureCollection", features: lines.slice(0, i + 1).map((l) => l.f) });
    const el = document.createElement("div"); el.className = "ring-label"; el.textContent = `${lines[i].m} m`;
    S.ringMarkers.push(new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat(lines[i].top).addTo(map));
    requestAnimationFrame(() => el.classList.add("on"));
    if (++i < lines.length) S.ringTimer = setTimeout(next, 120);
  };
  map.getSource("rings").setData(empty());
  if (lines.length) S.ringTimer = setTimeout(next, 250);
}
function toLines(g) {
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  return { type: "MultiLineString", coordinates: polys.flatMap((p) => p) };
}
function topPoint(g) {
  const pts = (g.type === "Polygon" ? g.coordinates : g.coordinates.flat(1)).flat(1);
  return pts.reduce((a, p) => (p[1] > a[1] ? p : a), pts[0]);
}

function chips(ids, ev) {
  return `<span class="chips">${(ids || []).map((id) => {
    const e = ev[id];
    const title = esc(e ? e.summary : id);
    return e && e.url ? `<a class="chip" href="${esc(e.url)}" target="_blank" rel="noopener" title="${title}">${esc(id)}</a>`
                      : `<span class="chip" title="${title}">${esc(id)}</span>`;
  }).join("")}</span>`;
}

const EN_TYPE = { "Föryngringsavverkning": "regeneration felling" };
const EN_STATUS = { "Anmält för avverkning": "notified for felling" };
const TOOL_SHORT = { landscape_context: "context", get_notification: "notification", check_completed_felling: "fellings", query_species: "species",
                     observation_effort: "effort", redlist_lookup: "red list", get_evidence: "evidence", record_finding: "verdict" };

function renderDossier(d) {
  const n = d.notification, ev = d.evidence, p = d.priority;
  const list = (items) => items.map((r) => `<li>${esc(r.claim)} ${chips(r.evidence_ids, ev)}</li>`).join("");
  // Why: species first; reasons that only restate notification facts go last (those facts are in the header)
  const META = new Set(["notification", "completed_felling"]);
  const order = d.reasons.map((r, i) => ({ r, kinds: d.reason_kinds[i] || [] }))
    .map((x) => ({ ...x, rank: x.kinds.includes("redlisted_species") ? 0 : x.kinds.every((k) => META.has(k)) ? 2 : 1 }))
    .sort((a, b) => a.rank - b.rank);
  const SHOW_REASONS = 1;
  const reasons = order.map((x, i) => `<li class="${x.rank === 2 ? "meta" : ""}${i >= SHOW_REASONS ? " more" : ""}">${esc(x.r.claim)} ${chips(x.r.evidence_ids, ev)}</li>`).join("");
  const nMoreReasons = Math.max(0, order.length - SHOW_REASONS);
  const spAll = d.species_lines || [];
  const nElig = spAll.filter((s) => s.eligible).length;
  const spShow = nElig >= 3 ? nElig : nElig + 2;
  const nMoreSp = Math.max(0, spAll.length - spShow);
  const spLines = spAll.map((s, i) => ({ s, more: i >= spShow })).map(({ s, more }) => `<li class="sp-line${s.eligible ? " elig" : ""}${more ? " more" : ""}"><span class="cat c-${s.category}">${s.category}</span> ${esc(s.swedish ? s.swedish + ", " : "")}${esc(s.group)} (<i>${esc(s.scientific)}</i>), ${s.dist_m} m${s.mobility === "mobile" ? ' <span class="tag dim">mobile</span>' : ""} ${chips([s.evidence_id], ev)}</li>`).join("");
  const c = d.context;
  const ctx = c ? `<div class="d-sec context"><h3>Context <span class="muted small">computed, not used for priority</span></h3>
      <div>Within 1 km: <b>${c.felled_ha.since_2015} ha</b> felled since 2015 · ${c.felled_ha.before_2015} ha before 2015 (zone ${c.zone_ha} ha)</div>
      ${c.later_fellings.length ? `<ul>${c.later_fellings.map((x) => `<li>${esc(x.swedish || x.species)} (${x.category}): ${x.n === 1 ? "the record" : x.n + " records"} from ${x.record_years.join(", ")} ${x.n === 1 ? "lies" : "lie"} in an area felled in ${fmtDate(x.felled)}; the habitat may be gone. <a class="chip" href="${esc(x.url)}" target="_blank" rel="noopener">GBIF</a></li>`).join("")}</ul>` : ""}
      <div class="hint-line">${esc(c.computed)}.</div></div>` : "";
  const path = d.path.map((s) => `<span class="step${s.error ? " err" : ""}">${esc(TOOL_SHORT[s.tool] || s.tool)}${s.args.buffer_m !== undefined ? " " + s.args.buffer_m + "m" : ""}${s.error ? " ✕" : ""}</span>`).join('<span class="arrow">›</span>');
  const urgent = p === "HIGH" || p === "MEDIUM";
  $("dossier-body").innerHTML = `
    <span class="badge p-${p}">${LABEL[p]}</span>${d.forced ? ' <span class="chip">harness-recorded</span>' : ""}
    <div class="d-id mono">${esc(d.beteckn)}</div>
    <div class="d-meta">${esc(titleCase(n.kommun))} · <b>${esc(n.polygon_ha)} ha</b> ${esc(EN_TYPE[n.avverktyp] || n.avverktyp)} · ${esc(n.skogstyp)} · <span title="${esc(n.status)}">${esc(EN_STATUS[n.status] || n.status)}</span> · received ${fmtDate(n.inkomdatum)}</div>
    <div class="window">
      <div class="label">${urgent ? "Field check before" : "Window closes"}</div>
      <div class="date">${fmtDate(d.window_closes)}</div>
      <div class="note">Normal six-week waiting period: received ${fmtDate(n.inkomdatum, false)} + 42 days.</div>
    </div>
    ${d.override_reason ? `<div class="override">Agent disagreed with the rule (${LABEL[d.rubric_hint.priority]}): ${esc(d.override_reason.text)} ${chips(d.override_reason.evidence_ids, ev)}</div>` : ""}
    <div class="d-sec why"><h3>Why</h3>${spLines ? `<ul class="sp-lines collapsed">${spLines}</ul>${nMoreSp ? `<button class="more-btn" data-t="sp">+${nMoreSp} more red-listed species</button>` : ""}` : ""}
      <ul class="reasons collapsed">${reasons}</ul>${nMoreReasons ? `<button class="more-btn" data-t="reasons">All ${order.length} reasons</button>` : ""}
      <div class="hint-line">Rule: ${LABEL[d.rubric_hint.priority]}, ${esc(d.rubric_hint.rule)} ${chips(d.rubric_hint.evidence_ids, ev)}</div></div>
    ${d.contradictions.length ? `<div class="d-sec"><h3>Contradictions</h3><ul>${list(d.contradictions)}</ul></div>` : ""}
    ${ctx}
    <div class="d-sec not-proven"><h3>What this does not prove</h3><ul>${d.not_established.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <div class="d-sec"><h3>Next action</h3><div>${esc(d.next_action)}</div></div>
    <div class="d-sec"><h3>Agent path</h3><div class="path">${path || '<span class="muted">not recorded</span>'}</div></div>
    <div class="d-sec"><h3>Uncertainties</h3><ul>${d.uncertainties.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <button class="live" id="live-btn">Investigate live <kbd>L</kbd></button>
    <div class="hint-line mono">${esc(d.model || "")} · ${esc(d.route || "")} · ${esc((d.recorded_at || "").replace("T", " "))}</div>`;
  $("live-btn").onclick = () => investigateLive(d.beteckn);
  document.querySelectorAll(".more-btn").forEach((b) => (b.onclick = () => {
    const ul = b.dataset.t === "sp" ? document.querySelector(".sp-lines") : document.querySelector(".reasons");
    ul.classList.remove("collapsed"); b.remove();
  }));
  $("dossier").scrollTop = 0;
}
const titleCase = (s) => String(s || "").toLowerCase().replace(/(^|[\s-])\p{L}/gu, (m) => m.toUpperCase());

function closeDossier() {
  document.body.classList.remove("dossier-open");
  $("dossier").setAttribute("aria-hidden", "true");
  if (!S.mapReady) return;
  setSelected(null); S.current = null;
  for (const src of ["rings", "species", "halos", "fellings"]) S.map.getSource(src).setData(empty());
  S.ringMarkers.forEach((m) => m.remove()); S.ringMarkers = [];
  history.replaceState(null, "", "#");
}

function stepSite(dir) {
  const order = S.sites.map((s) => s.beteckn);
  const i = S.current ? order.indexOf(S.current) : -1;
  selectSite(order[(i + dir + order.length) % order.length]);
}

/* ------------------------------------------------------------------ replay */
function feed(html, cls = "") {
  const li = document.createElement("li"); li.className = cls; li.innerHTML = html;
  $("feed-list").appendChild(li);
  const list = $("feed-list"); list.scrollTop = list.scrollHeight;
  $("feed-hint").hidden = true;
}
function interval() { return Math.max(120, Math.min(400, 90000 / Math.max(1, S.replay.steps.length))); }

function resetReplay() {
  clearTimeout(S.timer);
  $("feed-intro").hidden = true;
  closeDossier();
  $("feed-list").innerHTML = "";
  S.sites.forEach((s) => setFade(s.beteckn, 0));
  S.step = 0; S.lastSite = null; S.finished = false;
}
function toggleReplay() {
  if (!S.replay || !S.mapReady) return;
  if (S.playing) return pauseReplay();
  if (S.step === 0 || S.finished) resetReplay();
  S.playing = true; document.body.classList.add("replaying");
  $("investigate").textContent = "Pause"; $("investigate").classList.add("playing");
  tick();
}
function pauseReplay() {
  S.playing = false; clearTimeout(S.timer);
  $("investigate").textContent = S.finished ? "Investigate this week" : "Resume"; $("investigate").classList.remove("playing");
  $("feed-status").textContent = S.finished ? "done" : `paused ${S.step}/${S.replay.steps.length}`;
}
function showStep(s, animate = true) {
  if (s.beteckn !== S.lastSite) {
    const site = S.byId[s.beteckn];
    feed(`<span class="mono">${esc(s.beteckn)}</span> · ${esc(titleCase(site.kommun))} · ${esc(site.area_ha)} ha`, "site");
    S.lastSite = s.beteckn;
    if (animate) S.map.flyTo({ center: site.centroid, zoom: 13.2, pitch: 40, duration: Math.min(1500, interval() * 3.2), essential: true });
    setSelected(s.beteckn); S.current = s.beteckn;
  }
  const site = S.byId[s.beteckn];
  feed(esc(s.text), s.final ? `verdict p-${site.priority}` : s.error ? "err" : "");
  if (s.final) animate ? animateFade(s.beteckn) : setFade(s.beteckn, 1);
}
function tick() {
  const steps = S.replay.steps;
  if (!S.playing) return;
  if (S.step >= steps.length) return finishReplay();
  showStep(steps[S.step]); S.step++;
  $("feed-status").textContent = `${S.step}/${steps.length}`;
  S.timer = setTimeout(tick, interval());
}
function finishReplay() {
  S.finished = true; S.playing = false; document.body.classList.remove("replaying");
  S.sites.forEach((s) => setFade(s.beteckn, 1)); // sites without a recorded path still show their verdict
  setSelected(null); S.current = null;
  feed(`Week investigated: ${S.week.investigated} sites, ${S.week.counts.HIGH || 0} HIGH, ${S.week.counts.MEDIUM || 0} MEDIUM, ${S.week.counts.UNDER_SURVEYED || 0} under-surveyed.`, "site");
  S.map.fitBounds(bbox(S.sites.map((s) => s.centroid)), { padding: pad(), pitch: 30, duration: 1800 });
  pauseReplay();
}
function jumpToStep(n) { // test hook (#replay=N): render the first N steps instantly, paused
  resetReplay();
  const steps = S.replay.steps.slice(0, Math.min(n, S.replay.steps.length));
  steps.forEach((s) => showStep(s, false));
  S.step = steps.length;
  document.body.classList.add("replaying");
  const last = steps[steps.length - 1];
  if (last) S.map.jumpTo({ center: S.byId[last.beteckn].centroid, zoom: 13.2, pitch: 40 });
  pauseReplay();
}

/* ------------------------------------------------------------------ live */
async function investigateLive(b) {
  if (S.liveBusy || !b) return;
  S.liveBusy = true;
  const btn = $("live-btn"); if (btn) btn.disabled = true;
  feed(`<span class="mono">${esc(b)}</span> · live investigation`, "site");
  try {
    const r = await fetch(`/api/live/${fileId(b)}`, { method: "POST" });
    if (!r.ok) throw new Error(String(r.status));
    let shown = 0;
    for (let i = 0; i < 120; i++) {
      await new Promise((res) => setTimeout(res, 600));
      const st = await getJSON("/api/live/status");
      st.lines.slice(shown).forEach((l) => feed(esc(l))); shown = st.lines.length;
      if (st.status === "done") { const d = await loadSite(b, true); feed(`Verdict: ${LABEL[d.priority]}.`, `verdict p-${d.priority}`); renderDossier(d); syncPriority(b, d.priority); break; }
      if (st.status === "error") throw new Error(st.error);
    }
  } catch (e) { // live unavailable: say so, then show this site's recorded investigation
    feed("Live run unavailable: showing the recorded run.", "site");
    (S.replay ? S.replay.steps.filter((s) => s.beteckn === b) : []).forEach((s) => feed(esc(s.text), s.final ? `verdict p-${S.byId[b].priority}` : ""));
  } finally { S.liveBusy = false; if ($("live-btn")) $("live-btn").disabled = false; }
}

function syncPriority(b, p) { // keep map colours and counters consistent with a fresh live dossier
  if (S.byId[b].priority === p) return;
  S.byId[b].priority = p;
  const fc = (geom) => ({ type: "FeatureCollection", features: S.sites.map((s) => ({ type: "Feature", geometry: geom(s), properties: { beteckn: s.beteckn, priority: s.priority } })) });
  S.map.getSource("sites").setData(fc((s) => s.polygon));
  S.map.getSource("dots").setData(fc((s) => ({ type: "Point", coordinates: s.centroid })));
  S.week.counts = S.sites.reduce((c, s) => ((c[s.priority] = (c[s.priority] || 0) + 1), c), {});
  renderCounters();
}

/* ------------------------------------------------------------------ evidence test tab */
function histogram(card, ev) {
  const pr = card.primary, k = pr.k;
  const counts = {};
  for (const [p, c] of Object.entries(pr.histogram)) counts[Math.round(parseFloat(p) * k)] = c;
  const maxHit = Math.max(pr.hits, ...Object.keys(counts).map(Number)) + 1;
  const maxC = Math.max(...Object.values(counts));
  const W = 560, H = 210, L = 34, B = 34, T = 18, bw = (W - L - 10) / (maxHit + 1);
  const y = (c) => T + (H - T - B) * (1 - c / maxC);
  let bars = "";
  for (let h = 0; h <= maxHit; h++) {
    const c = counts[h] || 0;
    bars += `<rect x="${L + h * bw + 2}" y="${y(c)}" width="${bw - 4}" height="${H - B - y(c)}" fill="${h >= pr.hits ? "#8C9AA0" : "#5E7067"}" rx="2"/>`;
    if (maxHit <= 16 || h % 2 === 0) bars += `<text x="${L + h * bw + bw / 2}" y="${H - B + 14}" fill="#9AABA1" font-size="14" text-anchor="middle" font-family="IBM Plex Mono">${h}</text>`;
  }
  const mx = L + (pr.baseline_mean * k) * bw + bw / 2, ox = L + pr.hits * bw + bw / 2;
  return `<svg viewBox="0 0 ${W} ${H + 8}" role="img" aria-label="Baseline histogram">
    ${bars}
    <line x1="${mx}" x2="${mx}" y1="${T - 6}" y2="${H - B}" stroke="#E8EFE9" stroke-dasharray="3 3" stroke-width="1.2"/>
    <text x="${mx + 4}" y="${T}" fill="#E8EFE9" font-size="14" font-family="IBM Plex Sans">random mean ${(pr.baseline_mean * k).toFixed(1)}</text>
    <line x1="${ox}" x2="${ox}" y1="${T - 10}" y2="${H - B}" stroke="#CFE3C8" stroke-width="3"/>
    <text x="${ox - 4}" y="${T + 16}" fill="#CFE3C8" font-size="15" font-weight="600" text-anchor="end" font-family="IBM Plex Sans">our rubric: ${pr.hits}</text>
    <text x="${L}" y="${H + 6}" fill="#9AABA1" font-size="13" font-family="IBM Plex Sans">sites near a pre-${ev.leakage.positive_requires_datinv_before.slice(0, 4)} key habitat among ${k} picks (${ev.draws.toLocaleString("en")} area-matched random draws)</text>
  </svg>`;
}
function renderEvidence() {
  const ev = S.ev; if (!ev) return;
  const lifts = [...ev.cards].sort((a, b) => (b.pre_registered ? 1 : 0) - (a.pre_registered ? 1 : 0))
    .map((c) => `${c.primary.lift.toFixed(1)}× ${c.pre_registered ? "in a pre-registered replication" : "at an exploratory cut-off"} (${c.county})`).join(" and ");
  $("ev-header").textContent = `Blind, scored once: our evidence ranking surfaced sites near known key habitats ${lifts}, compared with area-matched random picks.`;
  $("ev-sub").textContent = `Positive = ${ev.label}. The key-habitat layer is hidden from the agent and the rule; each county was scored once. Ranking = the deterministic rubric over every regeneration-felling notification in the window.`;
  const big = [...ev.cards].sort((a, b) => (b.pre_registered ? 1 : 0) - (a.pre_registered ? 1 : 0));
  $("ev-big").innerHTML = big.map((c) => `<div class="big"><div class="num">${c.primary.lift.toFixed(1)}×</div>
      <div class="lab"><b>${esc(c.county)}</b> · ${c.pre_registered ? "pre-registered replication" : "exploratory cut-off"}<br>
      <span class="muted">top ${c.primary.k} of ${c.n_total}: ${c.primary.hits} near a key habitat vs ${(c.primary.baseline_mean * c.primary.k).toFixed(1)} expected · p = ${c.primary.p_value.toFixed(3)}</span></div></div>`).join("");
  $("ev-cards").innerHTML = ev.cards.map((c) => `
    <div class="card">
      <h2>${esc(c.county)}${c.pre_registered ? ' <span class="chip">pre-registered replication</span>' : ""}</h2>
      <div class="meta">Received ${fmtDate(c.window[0], false)}–${fmtDate(addDays(c.window[1], -1))} · ${c.excluded} excluded (only key habitats inventoried ${ev.leakage.positive_requires_datinv_before.slice(0, 4)} or later, or undated, nearby) · scored with <span class="mono">${esc(c.scored_with_commit)}</span></div>
      ${histogram(c, ev)}
      <div class="stats">
        <div>N<b>${c.n_eval}</b></div><div>positives<b>${c.positives}</b></div><div>k (10% of ${c.n_total})<b>${c.primary.k}</b></div><div>precision<b>${c.primary.precision_at_k.toFixed(2)}</b></div>
        <div>baseline mean<b>${c.primary.baseline_mean.toFixed(3)}</b></div><div>baseline p95<b>${c.primary.baseline_p95.toFixed(3)}</b></div><div>p<b>${c.primary.p_value.toFixed(3)}</b></div><div class="lift">lift<b>${c.primary.lift.toFixed(1)}×</b></div>
      </div>
      ${c.note ? `<div class="secondary">${esc(c.note)}</div>` : ""}
      <div class="secondary">${c.secondary.map((s) => `Also k = ${s.k}: ${s.hits} hit${s.hits === 1 ? "" : "s"}, precision ${s.precision_at_k.toFixed(2)}, baseline ${s.baseline_mean.toFixed(3)}, p ${s.p_value.toFixed(2)}, lift ${s.lift === null ? "undefined" : s.lift.toFixed(1) + "×"}.`).join(" ")}</div>
    </div>`).join("");
  const a = ev.agent_sample;
  $("ev-notes").innerHTML = [
    a.agent.hits === a.rubric_hint.hits
      ? `Agent-only sample in ${esc(a.county)} (${a.n_sampled} sites sampled, ${a.n_eval} scored, ${a.positives} positives): no measurable difference from the rule. Agent and rule both had ${a.agent.hits} of ${a.agent.k} in their top ${a.agent.k} (random baseline ${(a.agent.baseline_mean * a.agent.k).toFixed(1)}, p = ${a.agent.p_value.toFixed(2)}).`
      : `Agent-only sample in ${esc(a.county)} (${a.n_sampled} sites sampled, ${a.n_eval} scored, ${a.positives} positives): agent ${a.agent.hits} of ${a.agent.k} (p = ${a.agent.p_value.toFixed(2)}), rule ${a.rubric_hint.hits} of ${a.rubric_hint.k} (p = ${a.rubric_hint.p_value.toFixed(2)}).`,
    `Key habitats are an evaluation proxy, hidden from the agent. At the ${a.positives} positive sample sites in ${esc(a.county)}, ${a.positives_swamp_forest} of the nearest key habitats were swamp forests, which the species evidence did not pick up.`,
    `Species records are used from ${ev.leakage.species_records_since_year} on and positives need a key habitat inventoried before ${ev.leakage.positive_requires_datinv_before.slice(0, 4)}: strong temporal separation, not perfect independence.`,
    `Pre-registration commits: protocol <span class="mono">${esc(ev.protocol_commit)}</span> (${esc(ev.cards[0].county)}), replication <span class="mono">${esc(ev.cards[1] ? ev.cards[1].scored_with_commit : "")}</span> (${esc(ev.cards[1] ? ev.cards[1].county : "")}).`,
  ].map((x) => `<li>${x}</li>`).join("");
}

/* ------------------------------------------------------------------ condense tab */
function renderCondense() {
  const c = S.condense, el = $("condense-body");
  if (!c || c.status === "pending" || !c.routes) {
    el.innerHTML = `<h1>Condense</h1><p class="ev-sub">We compress the investigation, never the evidence.</p>
      <div class="pending">A/B measurement pending: the same 10 notifications, direct vs through Condense.</div>`;
    return;
  }
  const d = c.routes.direct, k = c.routes.condense, sv = c.savings, ag = c.agreement;
  const bar = (label, a, b, fmt) => {
    const m = Math.max(a, b);
    return `<div class="bar-row"><span>${label}, direct</span><div class="bar" style="width:${(100 * a) / m}%"></div><span class="mono">${fmt(a)}</span></div>
            <div class="bar-row"><span>${label}, via Condense</span><div class="bar condense" style="width:${(100 * b) / m}%"></div><span class="mono">${fmt(b)}</span></div>`;
  };
  const signed = (x, less, more) => `${Math.abs(x).toFixed(0)}% ${x >= 0 ? less : more}`;
  const n = (x) => x.toLocaleString("en");
  el.innerHTML = `<h1>Condense</h1><p class="ev-sub">We compress the investigation, never the evidence. ${c.pairs} paired runs of the same ${c.sites.length} Dalarna notifications, direct vs through Condense, ${esc(c.model)}.</p>
    <div class="ev-big">
      <div class="big"><div class="num">${ag.same_priority}/${ag.of}</div><div class="lab"><b>same priorities</b><br><span class="muted">site runs, direct vs Condense</span></div></div>
      <div class="big"><div class="num">${signed(sv.input_per_call_pct, "", "").trim()}</div><div class="lab"><b>${sv.input_per_call_pct >= 0 ? "fewer" : "more"} input tokens per call</b><br><span class="muted">${n(d.input_per_call)} → ${n(k.input_per_call)}</span></div></div>
      <div class="big"><div class="num">${ag.cited_ids_found_in_evidence_store}</div><div class="lab"><b>cited evidence IDs retained</b><br><span class="muted">lossless evidence store</span></div></div>
    </div>
    <div class="bars">${bar("Input tokens, total", d.input_total, k.input_total, n)}
    ${bar("Model calls", d.calls, k.calls, n)}
    ${bar("Cost, total", d.usd, k.usd, (x) => "$" + x.toFixed(3))}</div>
    <p class="cond-sum">${esc(ag.summary)}</p>
    <ul class="footnotes">
      <li>Per call, Condense sent ${signed(sv.input_per_call_pct, "fewer", "more")} input tokens and cost ${signed(sv.usd_per_call_pct, "less", "more")}. In total the agent made ${k.calls} calls through Condense against ${d.calls} direct, so the total cost was ${signed(sv.usd_pct, "lower", "higher")}.</li>
      <li>Uncached input: ${n(d.input_uncached)} direct vs ${n(k.input_uncached)} via Condense; cache reads ${n(d.cache_read)} vs ${n(k.cache_read)}. Compression changes the prompt prefix, so some cache hits are lost.</li>
      <li>${esc(c.note)}</li>
    </ul>`;
}

/* ------------------------------------------------------------------ tabs, keys, hash */
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("view-map").hidden = name !== "map"; $("view-evidence").hidden = name !== "evidence"; $("view-condense").hidden = name !== "condense";
  if (name === "map" && S.map) S.map.resize();
}
function bindUI() {
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
  $("investigate").onclick = () => { showTab("map"); toggleReplay(); };
  $("dossier-close").onclick = closeDossier;
  $("about-open").onclick = () => $("about").classList.add("open");
  $("about-close").onclick = () => $("about").classList.remove("open");
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea")) return;
    if (e.key === " ") { e.preventDefault(); showTab("map"); toggleReplay(); }
    else if (e.key === "ArrowRight") { if (!S.playing) stepSite(1); }
    else if (e.key === "ArrowLeft") { if (!S.playing) stepSite(-1); }
    else if (e.key === "1") showTab("map");
    else if (e.key === "2") showTab("evidence");
    else if (e.key === "3") showTab("condense");
    else if (e.key.toLowerCase() === "l") investigateLive(S.current);
    else if (e.key.toLowerCase() === "f") { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {}); }
    else if (e.key === "Escape") { closeDossier(); $("about").classList.remove("open"); }
  });
}
let hashApplied = false;
function applyHash() {
  if (hashApplied || !S.mapReady || !S.replay) return;
  hashApplied = true;
  if (params.get("tab")) showTab(params.get("tab"));
  if (params.get("replay")) jumpToStep(parseInt(params.get("replay"), 10));
  else if (params.get("site")) selectSite(params.get("site").replace(/_/g, " "));
}

boot().catch((e) => { console.error(e); $("counters").textContent = "Data not built yet: run scripts/build_web_data.py"; });
