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
  S.all = week.sites; S.sites = week.sites; S.all.forEach((s) => (S.byId[s.beteckn] = s));
  S.filter = { lannr: "", from: week.received_from, to: week.received_to };
  renderTools(); renderCounters(); renderHeadline(); renderIntro(); renderCounties();
  initMap();
  Promise.all([getJSON("data/replay.json"), getJSON("data/evidence_test.json"), getJSON("data/condense.json")])
    .then(([replay, ev, condense]) => { S.replay = replay; if (S.week.scope === "Sweden") S.replay.steps = interleave(replay.steps); S.allSteps = S.replay.steps; S.ev = ev; S.condense = condense; renderEvidence(); renderCondense(); applyHash(); })
    .catch((e) => console.warn("secondary data:", e.message));
}

function renderCounters() {
  const w = S.week, c = countBy(S.sites);
  const nAgent = S.sites.filter((s) => s.kind !== "rule").length;
  const part = (cls, n, label) => `<span class="${cls}"><b>${n ?? 0}</b> ${label}</span>`;
  const extra = w.investigated < w.notified ? `<span class="dot">·</span>${part("", w.investigated, "investigated")}` : "";
  $("feed-scope").textContent = `${w.county} · ${fmtDate(w.received_from, false)}–${fmtDate(w.received_to, false)}`;
  const national = w.scope === "Sweden";
  $("counters").innerHTML = [
    part("", S.sites.length, national ? "notified" : "notified this week"), national ? "" : extra,
    `<span class="dot">·</span>`, part("c-high", c.HIGH, "HIGH"), `<span class="dot">·</span>`, part("c-medium", c.MEDIUM, "MEDIUM"),
    `<span class="dot">·</span>`, part("c-under", c.UNDER_SURVEYED, "under-surveyed"),
    national ? `<span class="dot">·</span>${part("", nAgent, "agent dossiers")}` : "",
  ].join(" ");
}

function renderIntro() {
  const allHigh = S.sites.filter((s) => s.priority === "HIGH");
  const highs = allHigh.slice(0, 12);
  $("feed-intro").innerHTML = `
    <div class="intro-h">How it works</div>
    <ol class="steps">
      <li>Reads each new felling notification and checks whether the site is already felled.</li>
      <li>Cross-examines species records (Artportalen via GBIF), the Swedish Red List 2025 and how much anyone has recorded nearby.</li>
      <li>Writes a dossier that cites its evidence and sets a priority for human review, or says the site is under-surveyed.</li>
      ${S.week.scope === "Sweden" ? "<li>Every other site gets a rule-only profile: the deterministic rubric over the same evidence, no agent.</li>" : ""}
    </ol>
    <div class="intro-h">This week's HIGH sites (${allHigh.length}${allHigh.length > highs.length ? `, top ${highs.length} shown` : ""})</div>
    <ul class="high-list">${highs.map((s) => `<li><button class="high-item" data-b="${esc(s.beteckn)}"><span class="mono">${esc(s.beteckn)}</span> · ${esc(titleCase(s.kommun))}${s.county ? ", " + esc(s.county) : ""}${s.kind === "agent" ? ' <span class="kind-tag">agent</span>' : ' <span class="kind-tag rule">rule</span>'}<span class="hl">${esc(s.headline)}</span></button></li>`).join("")}</ul>`;
  document.querySelectorAll(".high-item").forEach((el) => (el.onclick = () => { showTab("map"); selectSite(el.dataset.b); }));
  if (S.week.scope === "Sweden") $("feed-hint").innerHTML = `Press <kbd>Space</kbd> or <b>Investigate this week</b> to replay the agent's ${S.week.agent_dossiers} investigations; the other ${S.week.notified - S.week.agent_dossiers} sites are rule-only profiles.`;
}

const countBy = (arr) => arr.reduce((c, s) => ((c[s.priority] = (c[s.priority] || 0) + 1), c), {});
function inDates(s) { return (!S.filter.from || s.received >= S.filter.from) && (!S.filter.to || s.received <= S.filter.to); }

function renderTools() {
  const w = S.week;
  if (w.scope !== "Sweden") return;
  const opts = (w.counties || []).slice().sort((a, b) => a.county.localeCompare(b.county, "sv"))
    .map((c) => `<option value="${esc(c.lannr)}">${esc(c.county)}</option>`).join("");
  $("tools").innerHTML = `
    <div class="search"><input id="q" type="search" placeholder="Search a site, municipality, forest, park…" autocomplete="off"><div class="results" id="results" hidden></div></div>
    <select id="region" title="Region"><option value="">All of Sweden</option>${opts}</select>
    <input id="d-from" type="date" value="${esc(S.filter.from)}" title="Received from">
    <input id="d-to" type="date" value="${esc(S.filter.to)}" title="Received to">
    <span class="data-note" id="data-note"></span>`;
  $("region").onchange = () => { S.filter.lannr = $("region").value; applyFilter(true); };
  $("d-from").onchange = () => { S.filter.from = $("d-from").value; applyFilter(false); };
  $("d-to").onchange = () => { S.filter.to = $("d-to").value; applyFilter(false); };
  let t = null;
  $("q").oninput = () => { clearTimeout(t); t = setTimeout(() => search($("q").value), 350); };
  $("q").onkeydown = (e) => { if (e.key === "Enter") { clearTimeout(t); search($("q").value, true); } if (e.key === "Escape") $("results").hidden = true; };
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) $("results").hidden = true; });
  dataNote();
}
function dataNote() {
  const w = S.week, outside = (S.filter.from && S.filter.from < w.received_from) || (S.filter.to && S.filter.to > w.received_to);
  $("data-note").innerHTML = outside
    ? `<button class="scan-btn" id="scan-btn">Scan ${esc(regionName())}, ${esc(S.filter.from)}–${esc(S.filter.to)}</button>`
    : `<span class="muted" title="Using the latest collected data">Data: ${fmtDate(w.received_from, false)}–${fmtDate(w.received_to, false)}${w.built_at ? ", built " + esc(w.built_at.slice(5)) : ""}</span>
       <button class="scan-btn small" id="scan-btn" title="Fetch the newest notifications for this region (no LLM)">Refresh ${esc(regionName())}</button>`;
  $("scan-btn").onclick = () => startScan();
}
const regionName = () => (S.filter.lannr ? ((S.week.counties || []).find((c) => c.lannr === S.filter.lannr) || {}).county : "Sweden");

function applyFilter(zoom) {
  S.sites = S.all.filter((s) => (!S.filter.lannr || s.lannr === S.filter.lannr) && inDates(s));
  const keep = new Set(S.sites.map((s) => s.beteckn));
  if (S.allSteps) S.replay.steps = S.allSteps.filter((s) => keep.has(s.beteckn));
  if (S.mapReady) {
    const fc = (geom) => ({ type: "FeatureCollection", features: S.sites.map((s) => ({ type: "Feature", geometry: geom(s), properties: { beteckn: s.beteckn, priority: s.priority } })) });
    S.map.getSource("sites").setData(fc((s) => s.polygon));
    S.map.getSource("dots").setData(fc((s) => ({ type: "Point", coordinates: s.centroid })));
    if (zoom && S.sites.length) S.map.fitBounds(bbox(S.sites.map((s) => s.centroid)), { padding: ovPad(), maxZoom: 10, duration: 1000 });
  }
  renderCounters(); renderIntro(); renderCounties(); dataNote();
  $("feed-scope").textContent = `${regionName()} · ${fmtDate(S.filter.from, false)}–${fmtDate(S.filter.to, false)}`;
}

async function search(q, enter = false) {
  q = q.trim();
  const box = $("results");
  if (q.length < 2) { box.hidden = true; return; }
  const ql = q.toLowerCase();
  const local = S.all.filter((s) => s.beteckn.toLowerCase().includes(ql) || (s.kommun || "").toLowerCase().includes(ql)).slice(0, 6)
    .map((s) => ({ kind: "site", label: `${s.beteckn} · ${titleCase(s.kommun)}, ${s.county}`, b: s.beteckn, p: s.priority }));
  const counties = (S.week.counties || []).filter((c) => c.county.toLowerCase().includes(ql)).map((c) => ({ kind: "county", label: `${c.county} (county)`, l: c.lannr }));
  let places = [];
  if (enter || (!local.length && !counties.length) || q.length >= 4) {
    try { places = (await getJSON(`/api/geocode?q=${encodeURIComponent(q)}`)).map((r) => ({ kind: "place", label: r.name, bbox: r.bbox, type: r.type })); } catch { /* offline: local results only */ }
  }
  const all = [...counties, ...local, ...places];
  box.innerHTML = all.length ? all.map((r, i) => `<button class="res" data-i="${i}"><span class="rk">${r.kind === "site" ? LABEL[r.p] : r.kind === "county" ? "county" : esc(r.type || "place")}</span>${esc(r.label)}</button>`).join("")
                             : `<div class="res muted">No matches</div>`;
  box.hidden = false;
  box.querySelectorAll(".res[data-i]").forEach((el) => (el.onclick = () => {
    const r = all[+el.dataset.i]; box.hidden = true; showTab("map");
    if (r.kind === "site") selectSite(r.b);
    else if (r.kind === "county") { $("region").value = r.l; S.filter.lannr = r.l; applyFilter(true); }
    else { closeDossier(); S.map.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], { padding: ovPad(), maxZoom: 13, duration: 1200 }); }
  }));
}

async function startScan() {
  const body = { lannr: S.filter.lannr || null, from: S.filter.from, to: S.filter.to };
  $("feed-intro").hidden = true;
  feed(`Scanning ${esc(regionName())}, ${esc(body.from)}–${esc(body.to)}: fetching notifications and building evidence profiles (no LLM)…`, "site");
  try {
    const r = await fetch("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(r.status === 409 ? "a scan is already running" : `scan failed (${r.status})`);
    let shown = 0;
    for (let i = 0; i < 900; i++) {
      await new Promise((res) => setTimeout(res, 1500));
      const st = await getJSON("/api/scan/status");
      st.lines.slice(shown).forEach((l) => feed(esc(l))); shown = st.lines.length;
      if (st.status === "done") { await reloadWeek(); break; }
      if (st.status === "error") throw new Error(st.error);
    }
  } catch (e) { feed(`Scan unavailable: ${esc(e.message)}. The map keeps the collected data.`, "err"); }
}
async function reloadWeek() {
  const week = await getJSON("data/week.json");
  S.week = week; S.all = week.sites; S.byId = {}; S.all.forEach((s) => (S.byId[s.beteckn] = s));
  applyFilter(true);
}

function renderCounties() {
  const w = S.week;
  if (!w.counties) { $("counties").hidden = true; return; }
  document.body.classList.add("has-counties");
  const countyRows = () => w.counties.map((c) => {
    const cs = S.all.filter((s) => s.lannr === c.lannr && inDates(s));
    return { ...c, notified: cs.length, counts: countBy(cs), agent: cs.filter((s) => s.kind !== "rule").length };
  }).filter((c) => c.notified).sort((a, b) => b.notified - a.notified);
  const c = (x, p) => x.counts[p] || 0;
  $("counties").innerHTML = `<div class="panel-head"><span>Counties this week</span><button class="collapse" id="cty-collapse" title="Hide">›</button></div>
    <div class="cty-note">${esc(w.rubric_note)} Priorities: deterministic rubric for all sites; the agent reviewed ${S.sites.filter((s) => s.kind !== "rule").length} of ${S.sites.length} shown.</div>
    <table class="cty"><thead><tr><th>County</th><th>notified</th><th class="h">HIGH</th><th class="m">MED</th><th class="u">under-surv.</th><th>agent</th></tr></thead>
    <tbody>${countyRows().map((x) => `<tr data-l="${esc(x.lannr)}"><td>${esc(x.county)}</td><td>${x.notified}</td><td class="h">${c(x, "HIGH")}</td><td class="m">${c(x, "MEDIUM")}</td><td class="u">${c(x, "UNDER_SURVEYED")}</td><td>${x.agent}</td></tr>`).join("")}</tbody></table>`;
  $("cty-collapse").onclick = () => toggleCounties(false);
  document.querySelectorAll(".cty tbody tr").forEach((tr) => (tr.onclick = () => {
    const pts = S.sites.filter((s) => s.lannr === tr.dataset.l).map((s) => s.centroid);
    if (pts.length) S.map.fitBounds(bbox(pts), { padding: pad(420), maxZoom: 10, duration: 1200 });
  }));
}

function toggleCounties(open) {
  S.ctyClosed = !open;
  document.body.classList.toggle("cty-closed", !open);
  $("counties").hidden = !open || document.body.classList.contains("dossier-open");
  $("cty-tab").hidden = open;
}

function renderHeadline() {
  const st = S.stats;
  if (!st || !st.headline) return;
  const sub = "Same measure over six weeks: " + st.zero_record_share.map((z) => `${z.county} ${pct(z.share_no_record_within_250m)}`).join(" · ");
  const wins = [...new Set(st.zero_record_share.map((z) => z.window.join()))];
  const win = st.zero_record_share[0].window;
  const when = wins.length === 1 ? `received ${fmtDate(win[0], false)}–${fmtDate(addDays(win[1], -1))}` : "in each county's six-week window";
  $("headline").innerHTML = `${esc(st.headline).replace(/(\d) m\b/g, "$1&nbsp;m")}<span class="sub">${esc(sub)} of notified regeneration-felling sites, ${when}</span>`;
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
    bounds: bbox(S.sites.map((s) => s.centroid)), fitBoundsOptions: { padding: ovPad() },
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

const ovPad = () => pad(document.body.classList.contains("has-counties") && !document.body.classList.contains("cty-closed") ? 420 : 0);
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
  map.addLayer({ id: "dots-glow", type: "circle", source: "dots", maxzoom: 12.5, filter: ["==", ["get", "priority"], "HIGH"],
                 paint: { "circle-color": COL.HIGH, "circle-radius": 11, "circle-blur": 1, "circle-opacity": ["*", 0.6, FADE] } });
  map.addLayer({ id: "dots", type: "circle", source: "dots", maxzoom: 12.5,
                 layout: { "circle-sort-key": ["match", ["get", "priority"], "HIGH", 4, "MEDIUM", 3, "LOW", 2, 1] },
                 paint: { "circle-color": ["case", ["<", FADE, 0.5], GREY, color], "circle-radius": ["case", ["<", FADE, 0.5], 2, ["match", ["get", "priority"], "HIGH", 5, "MEDIUM", 4, 3]],
                          "circle-opacity": ["case", ["<", FADE, 0.5], 0.45, ["==", ["get", "priority"], "UNDER_SURVEYED"], 0.35, ["==", ["get", "priority"], "ALREADY_FELLED"], 0, 0.95],
                          "circle-stroke-color": ["case", ["<", FADE, 0.5], GREY, color], "circle-stroke-width": 1.5,
                          "circle-stroke-opacity": ["case", ["<", FADE, 0.5], 0.4, 1] } });
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
    map.on("click", id, (e) => selectSite(e.features[0].properties.beteckn));
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
     <a href="https://www.gbif.org/occurrence/${esc(p.key)}" target="_blank" rel="noopener">GBIF record ${esc(p.key)}</a>`).addTo(S.map);
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
  $("counties").hidden = true;
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
  const spShow = Math.min(5, nElig >= 3 ? nElig : nElig + 2);
  const nMoreSp = Math.max(0, spAll.length - spShow);
  const spLines = spAll.map((s, i) => ({ s, more: i >= spShow })).map(({ s, more }) => `<li class="sp-line${s.eligible ? " elig" : ""}${more ? " more" : ""}"><span class="cat c-${s.category}">${s.category}</span> ${esc(s.swedish ? s.swedish + ", " : "")}${esc(s.group)} (<i>${esc(s.scientific)}</i>), ${s.dist_m}&nbsp;m${s.mobility === "mobile" ? ' <span class="tag dim">mobile</span>' : ""} ${chips([s.evidence_id], ev)}</li>`).join("");
  const photos = (d.species_lines || []).filter((s) => s.photo).slice(0, 2).map((s) => `
      <figure class="ph"><img src="data/${esc(s.photo.file)}" alt="${esc(s.swedish || s.scientific)}" loading="lazy">
        <figcaption><b>${esc(s.swedish || "")}</b> <i>${esc(s.scientific)}</i><span>${esc(s.photo.caption)}</span></figcaption></figure>`).join("");
  const isAgent = d.kind !== "rule";
  const banner = isAgent
    ? `<div class="kind agent">Agent dossier: Forest Witness investigated this site and cites its evidence.${d.rubric_status === "untested" ? " The rubric behind it is untested in this county." : ""}</div>`
    : `<div class="kind rule">Rule-only profile: the deterministic rubric applied to the evidence. No agent has reviewed this site${d.rubric_status === "untested" ? ", and the rubric is untested in this county" : ""}.</div>`;
  const c = d.context;
  const ctx = c ? `<div class="d-sec context"><h3>Context <span class="muted small">computed, not used for priority</span></h3>
      <div>Within 1 km: <b>${c.felled_ha.since_2015} ha</b> felled since 2015 · ${c.felled_ha.before_2015} ha before 2015 (zone ${c.zone_ha} ha)</div>
      ${c.later_fellings.length ? `<ul>${c.later_fellings.map((x) => `<li>${esc(x.swedish || x.species)} (${x.category}): ${x.n === 1 ? "the record" : x.n + " records"} from ${x.record_years.join(", ")} ${x.n === 1 ? "lies" : "lie"} in an area felled in ${fmtDate(x.felled)}; the habitat may be gone. <a class="chip" href="${esc(x.url)}" target="_blank" rel="noopener">GBIF</a></li>`).join("")}</ul>` : ""}
      <div class="hint-line">${esc(c.computed)}.</div></div>` : "";
  const path = d.path.map((s) => `<span class="step${s.error ? " err" : ""}">${esc(TOOL_SHORT[s.tool] || s.tool)}${s.args.buffer_m !== undefined ? " " + s.args.buffer_m + "m" : ""}${s.error ? " ✕" : ""}</span>`).join('<span class="arrow">›</span>');
  const urgent = p === "HIGH" || p === "MEDIUM";
  $("dossier-body").innerHTML = `
    <span class="badge p-${p}">${LABEL[p]}</span>${d.forced ? ' <span class="chip">harness-recorded</span>' : ""}
    ${banner}
    <div class="d-id mono">${esc(d.beteckn)}</div>
    <div class="d-meta">${esc(titleCase(n.kommun))} · <b>${esc(n.polygon_ha)} ha</b> ${esc(EN_TYPE[n.avverktyp] || n.avverktyp)} · ${esc(n.skogstyp)} · <span title="${esc(n.status)}">${esc(EN_STATUS[n.status] || n.status)}</span> · received ${fmtDate(n.inkomdatum)}</div>
    <div class="window">
      <div class="label">${urgent ? "Field check before" : "Window closes"}</div>
      <div class="date">${fmtDate(d.window_closes)}</div>
      <div class="note">Normal six-week waiting period: received ${fmtDate(n.inkomdatum, false)} + 42 days.</div>
    </div>
    ${d.override_reason ? `<div class="override">Agent disagreed with the rule (${LABEL[d.rubric_hint.priority]}): ${esc(d.override_reason.text)} ${chips(d.override_reason.evidence_ids, ev)}</div>` : ""}
    <div class="d-sec why"><h3>Why</h3>${photos ? `<div class="photos">${photos}</div>` : ""}${spLines ? `<ul class="sp-lines collapsed">${spLines}</ul>${nMoreSp ? `<button class="more-btn" data-t="sp">+${nMoreSp} more red-listed species</button>` : ""}` : ""}
      ${isAgent ? `<ul class="reasons collapsed">${reasons}</ul>${nMoreReasons ? `<button class="more-btn" data-t="reasons">All ${order.length} reasons</button>` : ""}` : ""}
      <div class="hint-line">Rule: ${LABEL[d.rubric_hint.priority]}, ${esc(d.rubric_hint.rule)} ${chips(d.rubric_hint.evidence_ids, ev)}</div></div>
    ${d.contradictions.length ? `<div class="d-sec"><h3>Contradictions</h3><ul>${list(d.contradictions)}</ul></div>` : ""}
    ${ctx}
    <div class="d-sec not-proven"><h3>⚠ Caution</h3><ul>${d.not_established.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <div class="d-sec"><h3>Next action</h3><div>${esc(d.next_action)}</div></div>
    <div class="d-sec"><h3>Agent path</h3><div class="path">${path || `<span class="muted">${isAgent ? "not recorded" : "no agent run yet"}</span>`}</div></div>
    <div class="d-sec"><h3>Uncertainties</h3><ul>${d.uncertainties.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <button class="live${isAgent ? "" : " primary-live"}" id="live-btn">${isAgent ? "Re-run the agent live" : "Run the agent on this site"} <kbd>L</kbd></button>
    <div class="chat" id="chat"></div>`;
  $("live-btn").onclick = () => investigateLive(d.beteckn);
  renderChat(d);
  document.querySelectorAll(".more-btn").forEach((b) => (b.onclick = () => {
    const ul = b.dataset.t === "sp" ? document.querySelector(".sp-lines") : document.querySelector(".reasons");
    ul.classList.remove("collapsed"); b.remove();
  }));
  $("dossier").scrollTop = 0;
}
const titleCase = (s) => String(s || "").toLowerCase().replace(/(^|[\s-])\p{L}/gu, (m) => m.toUpperCase());

function closeDossier() {
  document.body.classList.remove("dossier-open");
  if ($("counties")) $("counties").hidden = !S.week || !S.week.counties || S.ctyClosed;
  $("dossier").setAttribute("aria-hidden", "true");
  if (!S.mapReady) return;
  setSelected(null); S.current = null;
  for (const src of ["rings", "species", "halos", "fellings"]) S.map.getSource(src).setData(empty());
  S.ringMarkers.forEach((m) => m.remove()); S.ringMarkers = [];
  history.replaceState(null, "", "#");
  S.map.fitBounds(bbox(visibleSites().map((s) => s.centroid)), { padding: ovPad(), pitch: document.body.classList.contains("replaying") ? 0 : 30, duration: 900 });
}

function overview() {
  showTab("map");
  if (document.body.classList.contains("dossier-open")) closeDossier();
  else if (S.mapReady) S.map.fitBounds(bbox(visibleSites().map((s) => s.centroid)), { padding: ovPad(), pitch: 30, duration: 900 });
}
function visibleSites() { return S.sites.length ? S.sites : S.all; }

function stepSite(dir) {
  const order = S.sites.map((s) => s.beteckn);
  const i = S.current ? order.indexOf(S.current) : -1;
  selectSite(order[(i + dir + order.length) % order.length]);
}

/* ------------------------------------------------------------------ replay */
function feed(html, cls = "", b = null) {
  const li = document.createElement("li"); li.className = cls; li.innerHTML = html;
  if (b) { li.classList.add("clickable"); li.title = "Open this site"; li.onclick = () => selectSite(b); }
  $("feed-list").appendChild(li);
  const list = $("feed-list"); list.scrollTop = list.scrollHeight;
  $("feed-hint").hidden = true;
}
function interval() { return Math.max(95, Math.min(400, 90000 / Math.max(1, S.replay.steps.length))); }
function interleave(steps) { // one site at a time, rotating across counties so the whole country lights up
  const bySite = new Map();
  steps.forEach((s) => { if (!bySite.has(s.beteckn)) bySite.set(s.beteckn, []); bySite.get(s.beteckn).push(s); });
  const byCounty = new Map();
  for (const [b, ss] of bySite) { const l = (S.byId[b] && S.byId[b].lannr) || "?"; if (!byCounty.has(l)) byCounty.set(l, []); byCounty.get(l).push(ss); }
  const queues = [...byCounty.values()], out = [];
  while (queues.some((q) => q.length)) for (const q of queues) if (q.length) out.push(...q.shift());
  return out;
}

function resetReplay() {
  clearTimeout(S.timer);
  $("feed-intro").hidden = true;
  closeDossier();
  S.map.fitBounds(bbox(visibleSites().map((s) => s.centroid)), { padding: ovPad(), pitch: 0, duration: 900 });
  $("feed-list").innerHTML = "";
  S.sites.forEach((s) => setFade(s.beteckn, s.kind === "rule" ? 1 : 0)); // rule-only sites keep their rubric colour
  S.step = 0; S.lastSite = null; S.finished = false;
  if (S.week.scope === "Sweden") { const na = S.sites.filter((s) => s.kind !== "rule").length; feed(`Replaying the agent's ${na} investigations in ${esc(regionName())}. The other ${S.sites.length - na} sites are rule-only profiles and keep their rubric colour.`, "site"); }
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
    feed(`<span class="mono">${esc(s.beteckn)}</span> · ${esc(titleCase(site.kommun))} · ${esc(site.area_ha)} ha`, "site", s.beteckn);
    S.lastSite = s.beteckn;
  }
  const site = S.byId[s.beteckn];
  feed(esc(s.text).replace(/(\d) m\b/g, "$1&nbsp;m"), s.final ? `verdict p-${site.priority}` : s.error ? "err" : "");
  if (s.final) {
    if (animate) { setFade(s.beteckn, 1); pulse(s.beteckn); } else setFade(s.beteckn, 1);
  }
}
function pulse(b) { // short ring at the dot when its verdict lands; the camera never moves during replay
  const site = S.byId[b];
  const el = document.createElement("div");
  el.className = "pulse"; el.style.setProperty("--c", COL[site.priority]);
  const m = new maplibregl.Marker({ element: el }).setLngLat(site.centroid).addTo(S.map);
  setTimeout(() => m.remove(), 1100);
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
  const ag = S.sites.filter((s) => s.kind !== "rule"), n = (arr, p) => arr.filter((s) => s.priority === p).length, c = S.week.counts;
  feed(S.week.scope === "Sweden"
    ? `Forest Witness investigated ${ag.length} sites (${n(ag, "HIGH")} HIGH, ${n(ag, "MEDIUM")} MEDIUM, ${n(ag, "UNDER_SURVEYED")} under-surveyed). The rubric profiled all ${S.week.notified} (${c.HIGH || 0} HIGH, ${c.MEDIUM || 0} MEDIUM, ${c.UNDER_SURVEYED || 0} under-surveyed).`
    : `Week investigated: ${S.week.investigated} sites, ${c.HIGH || 0} HIGH, ${c.MEDIUM || 0} MEDIUM, ${c.UNDER_SURVEYED || 0} under-surveyed.`, "site");
  pauseReplay();
}
function jumpToStep(n) { // test hook (#replay=N): render the first N steps instantly, paused
  resetReplay();
  const steps = S.replay.steps.slice(0, Math.min(n, S.replay.steps.length));
  steps.forEach((s) => showStep(s, false));
  S.step = steps.length;
  document.body.classList.add("replaying");
  S.map.jumpTo({ center: S.map.getCenter(), pitch: 0 });
  pauseReplay();
}

/* ------------------------------------------------------------------ live */
async function investigateLive(b) {
  if (S.liveBusy || !b) return;
  S.liveBusy = true;
  const btn = $("live-btn"); if (btn) btn.disabled = true;
  feed(`<span class="mono">${esc(b)}</span> · live investigation`, "site", b);
  try {
    const r = await fetch(`/api/live/${fileId(b)}`, { method: "POST" });
    if (!r.ok) throw new Error(String(r.status));
    let shown = 0;
    for (let i = 0; i < 120; i++) {
      await new Promise((res) => setTimeout(res, 600));
      const st = await getJSON("/api/live/status");
      st.lines.slice(shown).forEach((l) => feed(esc(l))); shown = st.lines.length;
      if (st.status === "done") {
        const d = await loadSite(b, true);
        feed(`Verdict: ${LABEL[d.priority]}.`, `verdict p-${d.priority}`, b);
        if (S.current === b) { renderDossier(d); selectSite(b, { fly: false }); }
        syncPriority(b, d.priority); break;
      }
      if (st.status === "error") throw new Error(st.error);
    }
  } catch (e) { // live unavailable: say so, then show this site's recorded investigation
    const recorded = S.replay ? S.replay.steps.filter((s) => s.beteckn === b) : [];
    if (!recorded.length) { feed("Live run unavailable. No agent has run on this site yet; the rule-only profile stands.", "site"); return; }
    feed("Live run unavailable: showing the recorded run.", "site");
    (S.replay ? S.replay.steps.filter((s) => s.beteckn === b) : []).forEach((s) => feed(esc(s.text), s.final ? `verdict p-${S.byId[b].priority}` : ""));
  } finally { S.liveBusy = false; if ($("live-btn")) $("live-btn").disabled = false; }
}

function syncPriority(b, p) { // keep map colours and counters consistent with a fresh live dossier
  S.byId[b].kind = "agent";
  if (S.byId[b].priority === p) return;
  S.byId[b].priority = p;
  const fc = (geom) => ({ type: "FeatureCollection", features: S.sites.map((s) => ({ type: "Feature", geometry: geom(s), properties: { beteckn: s.beteckn, priority: s.priority } })) });
  S.map.getSource("sites").setData(fc((s) => s.polygon));
  S.map.getSource("dots").setData(fc((s) => ({ type: "Point", coordinates: s.centroid })));
  S.week.counts = S.sites.reduce((c, s) => ((c[s.priority] = (c[s.priority] || 0) + 1), c), {});
  renderCounters();
}

/* ------------------------------------------------------------------ chat about one site */
S.chats = {};
function renderChat(d) {
  const b = d.beteckn, el = $("chat");
  if (!el) return;
  const sp = (d.species_lines || [])[0];
  const sugg = [
    "Make a field-visit plan for this site before the window closes",
    sp ? `What should a reviewer know about ${sp.swedish || sp.scientific} (${sp.scientific})?` : "Which records matter most here?",
    "Chart the red-listed records near this site by year",
  ];
  el.innerHTML = `<h3>Ask about this site</h3>
    <div class="chat-log" id="chat-log"></div>
    <div class="chat-sugg">${sugg.map((q) => `<button class="sugg">${esc(q)}</button>`).join("")}</div>
    <form class="chat-form" id="chat-form"><textarea id="chat-in" rows="2" placeholder="Ask about this site, its species, or what to check in the field"></textarea><button type="submit" class="primary">Ask</button></form>`;
  (S.chats[b] || []).forEach((m) => chatBubble(m, d));
  el.querySelectorAll(".sugg").forEach((x) => (x.onclick = () => { if (!el.classList.contains("busy")) sendChat(d, x.textContent); }));
  $("chat-form").onsubmit = (e) => { e.preventDefault(); const q = $("chat-in").value.trim(); if (q && !el.classList.contains("busy")) sendChat(d, q); };
  $("chat-in").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chat-form").requestSubmit(); } };
}
function chatBubble(m, d) {
  const div = document.createElement("div");
  div.className = `msg ${m.role}`;
  div.innerHTML = m.role === "user" ? esc(m.content) : richText(m.content, d.evidence) + (m.sources && m.sources.length
    ? `<div class="srcs">Sources: ${m.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>`).join(" · ")}</div>` : "");
  $("chat-log").appendChild(div);
  div.scrollIntoView({ block: "nearest" });
  return div;
}
async function sendChat(d, q) {
  const b = d.beteckn;
  const hist = (S.chats[b] = S.chats[b] || []);
  hist.push({ role: "user", content: q });
  chatBubble(hist[hist.length - 1], d);
  $("chat-in").value = "";
  const box = $("chat"), t0 = Date.now();
  box.classList.add("busy");
  $("chat-in").disabled = true;
  const wait = document.createElement("div");
  wait.className = "msg assistant pending";
  wait.innerHTML = `<div class="typing"><span></span><span></span><span></span></div><span class="elapsed">0 s</span><div class="wait-hint" hidden>Deeper searches can take up to a minute.</div>`;
  $("chat-log").appendChild(wait);
  wait.scrollIntoView({ block: "nearest" });
  const tick = setInterval(() => {
    const sec = Math.round((Date.now() - t0) / 1000);
    const el = wait.querySelector(".elapsed"); if (el) el.textContent = `${sec} s`;
    const h = wait.querySelector(".wait-hint"); if (h && sec >= 15) h.hidden = false;
  }, 500);
  const done = () => { clearInterval(tick); box.classList.remove("busy"); $("chat-in").disabled = false; };
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beteckn: b, messages: hist.map(({ role, content }) => ({ role, content })) }) });
    if (!r.ok) throw new Error(r.status === 503 ? "Chat needs the live backend with an API key." : `Chat failed (${r.status}).`);
    const ans = await r.json();
    hist.push({ role: "assistant", content: ans.text, sources: ans.sources });
    const answer = chatBubble(hist[hist.length - 1], d);
    wait.replaceWith(answer);
    done();
  } catch (e) {
    done();
    wait.classList.remove("pending"); wait.innerHTML = esc(e.message);
    hist.pop();
  }
}
function richText(t, ev) {
  const parts = String(t).split(/```chart\s*([\s\S]*?)```/);
  return parts.map((p, i) => {
    if (i % 2 === 1) { try { return chartSVG(JSON.parse(p)); } catch { return ""; } }
    let h = esc(p)
      .replace(/\[(E-\d+)\]\((https?:[^)\s]+)\)/g, (m, id) => chips([id], ev || {}))
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`)
      .replace(/\[(E-\d+(?:,\s*E-\d+)*)\]/g, (m, ids) => chips(ids.split(/,\s*/), ev || {}))
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<i>$2</i>");
    const lines = h.split("\n"), out = [];
    let inList = false, table = [];
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)).map((r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      out.push(`<div class="tbl"><table>${rows.map((r, i) => `<tr>${r.map((c) => i === 0 ? `<th>${c}</th>` : `<td>${c}</td>`).join("")}</tr>`).join("")}</table></div>`);
      table = [];
    };
    for (const ln of lines) {
      if (/^\s*\|.*\|\s*$/.test(ln)) { if (inList) { out.push("</ul>"); inList = false; } table.push(ln); continue; }
      flushTable();
      const li = ln.match(/^\s*[-•]\s+(.*)/) || ln.match(/^\s*\d+\.\s+(.*)/);
      if (li) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${li[1]}</li>`); continue; }
      if (inList) { out.push("</ul>"); inList = false; }
      if (ln.trim().startsWith("#")) out.push(`<p><b>${ln.replace(/^#+\s*/, "")}</b></p>`);
      else if (ln.trim()) out.push(`<p>${ln}</p>`);
    }
    flushTable();
    if (inList) out.push("</ul>");
    return out.join("");
  }).join("");
}
function chartSVG(c) {
  const x = c.x || [], s = (c.series || [])[0] || { values: [] }, v = s.values.map(Number);
  if (!x.length || !v.length) return "";
  const W = 340, H = 170, L = 30, B = 34, T = 22, max = Math.max(...v, 1), bw = (W - L - 8) / x.length;
  const y = (n) => T + (H - T - B) * (1 - n / max);
  let g = "";
  if (c.type === "line") {
    const pts = v.map((n, i) => `${L + i * bw + bw / 2},${y(n)}`).join(" ");
    g += `<polyline points="${pts}" fill="none" stroke="#CFE3C8" stroke-width="2.5"/>` + v.map((n, i) => `<circle cx="${L + i * bw + bw / 2}" cy="${y(n)}" r="3" fill="#CFE3C8"/>`).join("");
  } else {
    g += v.map((n, i) => `<rect x="${L + i * bw + 3}" y="${y(n)}" width="${Math.max(2, bw - 6)}" height="${H - B - y(n)}" rx="2" fill="#8FBF9A"/>`).join("");
  }
  g += v.map((n, i) => `<text x="${L + i * bw + bw / 2}" y="${y(n) - 4}" font-size="10" fill="#E8EFE9" text-anchor="middle">${n}</text>`).join("");
  g += x.map((lab, i) => `<text x="${L + i * bw + bw / 2}" y="${H - B + 14}" font-size="10" fill="#9AABA1" text-anchor="middle">${esc(String(lab)).slice(0, 10)}</text>`).join("");
  return `<figure class="chart"><svg viewBox="0 0 ${W} ${H}"><text x="${L}" y="13" font-size="11.5" fill="#E8EFE9" font-weight="600">${esc(c.title || "")}</text>${g}
    <line x1="${L}" x2="${W - 4}" y1="${H - B}" y2="${H - B}" stroke="#26342D"/></svg><figcaption>${esc(c.unit || "")} · from this site's evidence</figcaption></figure>`;
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
  const cards = [...ev.cards].sort((a, b) => (b.pre_registered ? 1 : 0) - (a.pre_registered ? 1 : 0));
  const a = ev.agent_sample, pre = ev.leakage.positive_requires_datinv_before.slice(0, 4);
  const card = (c) => {
    const pr = c.primary, rnd = pr.baseline_mean * pr.k, max = Math.max(pr.hits, rnd, 1);
    const times = Math.max(1, Math.round((pr.p_share || pr.p_value) * 100));
    return `<div class="t-card">
      <div class="t-head"><h2>${esc(c.county)}</h2><span class="t-tag ${c.pre_registered ? "plan" : "first"}">${c.pre_registered ? "planned in advance" : "first look"}</span></div>
      <div class="t-meta">${c.n_total} new felling notifications, ${fmtDate(c.window[0], false)}–${fmtDate(addDays(c.window[1], -1))}</div>
      <div class="t-bars">
        <div class="t-row"><span class="t-lab">Our top ${pr.k} sites</span><div class="t-bar ours" style="width:${(100 * pr.hits) / max}%"></div><b>${pr.hits}</b></div>
        <div class="t-row"><span class="t-lab">Random sites, same sizes</span><div class="t-bar rnd" style="width:${(100 * rnd) / max}%"></div><b>${rnd.toFixed(1)}</b></div>
      </div>
      <div class="t-unit">sites within 250 m of a known valuable forest</div>
      <div class="t-result"><span class="t-x">${pr.lift.toFixed(1)}×</span> more often than chance.
        Random picks did as well only <b>${times} time${times === 1 ? "" : "s"} in 100</b>.</div>
      ${c.pre_registered ? "" : `<div class="t-note">The cut-off (top 10%) was one of two chosen before scoring but not named as the main one, so treat this county as a first look.</div>`}
    </div>`;
  };
  $("ev-body").innerHTML = `
    <h1>Does it point to the right places?</h1>
    <p class="t-lead">Skogsstyrelsen has already mapped forests it knows are valuable (key habitats, <i>nyckelbiotoper</i>). We hid that map, let WitnessWoods rank every new felling notification using only open species data, and then checked the hidden map.</p>
    <ol class="t-steps">
      <li><b>Hide the answer.</b> The map of known valuable forests is never shown to the ranking or the agent.</li>
      <li><b>Rank blind.</b> Every new notification in a county gets a priority from species records, the Red List and recording effort.</li>
      <li><b>Check once.</b> How many of our top 10% lie near a known valuable forest, compared with random sites of the same sizes?</li>
    </ol>
    <div class="t-cards">${cards.map(card).join("")}</div>
    <div class="t-limits"><h3>What this does and does not show</h3><ul>
      <li>It tests the <b>ranking rule</b> on two counties it was never tuned on. The AI agent's own test was small (${a.n_sampled} sites, ${a.positives} near a valuable forest) and showed no difference from the rule.</li>
      <li>Known valuable forests are a stand-in for value, not the full truth. In the agent's test, ${a.positives_swamp_forest} of the ${a.positives} valuable forests nearby were swamp forests, which the species records did not pick up.</li>
      <li>Each county was checked once, with the code fixed in advance. Nothing was tuned afterwards.</li>
    </ul></div>
    <details class="t-method"><summary>Method details</summary>
      <p>Positive = a key habitat inventoried before ${pre} within 250 m of the notified polygon; species records from ${ev.leakage.species_records_since_year} on, so the two are separated in time. Baseline = ${ev.draws.toLocaleString("en")} random draws of the same number of sites with the same mix of site areas. "Times in 100" = share of random draws that reached our number of hits. Code fixed before scoring: <span class="mono">${esc(ev.protocol_commit)}</span> (Gävleborg) and <span class="mono">${esc((cards.find((c) => c.pre_registered) || {}).scored_with_commit || "")}</span> (Värmland).</p>
    </details>`;
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
  $("view-map").hidden = name !== "map"; $("view-evidence").hidden = name !== "evidence"; $("view-condense").hidden = name !== "condense";
  if (name === "map" && S.map) S.map.resize();
}
function bindUI() {
  $("brand").onclick = overview;
  $("brand").onkeydown = (e) => { if (e.key === "Enter") overview(); };
  $("overview-btn").onclick = overview;
  $("evidence-open").onclick = () => showTab("evidence");
  $("evidence-back").onclick = () => showTab("map");
  $("cty-tab").onclick = () => toggleCounties(true);
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
    else if (e.key.toLowerCase() === "l") investigateLive(S.current);
    else if (e.key.toLowerCase() === "f") { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {}); }
    else if (e.key === "Escape") { $("about").classList.remove("open"); overview(); }
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
