"""Tiny backend: serves web/ (including the static web/data/ snapshot) and one optional live endpoint.

  .venv/bin/python server.py            -> http://127.0.0.1:8000

POST /api/live/{beteckn} re-investigates one site with the agent in a background thread (costs a few
cents); GET /api/live/status reports its progress. Replay mode needs neither, nor any API key.
"""
import json
import os
import sys
import threading
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fw import config  # noqa: E402

app = FastAPI(title="WitnessWoods")
TILE_SRC = {"eox": "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2023_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg",
            "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png"}
_http = httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=10, follow_redirects=True)
_live = {"status": "idle", "beteckn": None, "lines": [], "priority": None, "error": None}
_lock = threading.Lock()


def _run_live(beteckn: str) -> None:
    from fw.agent import Run
    from scripts import build_web_data as bwd
    from fw import rank, tools
    try:
        run = Run(config.FW_ROUTE, config.FW_MODEL, min(config.FW_BUDGET_USD, 0.5), "live")
        orig_log = run.log

        def log(event, line=None):  # mirror the agent's own log lines into the live feed
            orig_log(event, line)
            if event.get("type") == "tool_call":
                prof = tools._profile(beteckn)
                _live["lines"].append(bwd.feed_text(event, prof, None) if event["tool"] != "record_finding"
                                      else ("Dossier rejected by the validator; revising." if event.get("error")
                                            else "Recording the dossier."))
        run.log = log
        res = run.batch([beteckn])
        if not res:
            raise RuntimeError(run.status)
        d = rank.load_dossiers([beteckn])[0]
        prof = tools._profile(beteckn)
        events = [e for e in bwd.log_events() if e.get("type") == "tool_call" and e.get("notification") == beteckn
                  and e["run_id"] == d["run_id"]]
        bwd.load_photos()
        payload = bwd.site_payload(beteckn, d, prof, events)
        lst = config.OUT / "national_week.json"
        lannr = next((x["lannr"] for x in json.loads(lst.read_text()) if x["beteckn"] == beteckn), None) if lst.exists() else None
        payload["rubric_status"] = bwd.RUBRIC_STATUS.get(lannr, "untested") if lannr else None
        (bwd.WEB / "sites" / f"{beteckn.replace(' ', '_')}.json").write_text(json.dumps(payload, ensure_ascii=False))
        _live.update(status="done", priority=d["priority"])
    except Exception as e:  # the UI falls back to replay quietly
        _live.update(status="error", error=f"{type(e).__name__}: {e}"[:300])


@app.post("/api/live/{beteckn}")
def live(beteckn: str):
    beteckn = beteckn.replace("_", " ")
    if not (os.getenv("ANTHROPIC_API_KEY") and config.FW_MODEL):
        raise HTTPException(503, "live mode needs ANTHROPIC_API_KEY and FW_MODEL")
    with _lock:
        if _live["status"] == "running":
            raise HTTPException(409, "a live investigation is already running")
        _live.update(status="running", beteckn=beteckn, lines=[], priority=None, error=None)
    threading.Thread(target=_run_live, args=(beteckn,), daemon=True).start()
    return {"started": beteckn}


@app.post("/api/chat")
def chat(body: dict = Body(...)):
    """Follow-up questions about one site; the agent sees the full dossier and evidence context."""
    from fw import chat as fwchat
    b = str(body.get("beteckn", "")).replace("_", " ")
    msgs = body.get("messages") or []
    if not b or not msgs:
        raise HTTPException(400, "beteckn and messages are required")
    try:
        return fwchat.ask(b, msgs)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.get("/api/geocode")
def geocode(q: str):
    """Places in Sweden by name (forests, parks, villages...) via OpenStreetMap Nominatim, cached to disk."""
    from fw.cache import get_json
    q = q.strip()[:120]
    if len(q) < 2:
        return []
    try:
        res = get_json("https://nominatim.openstreetmap.org/search",
                       {"q": q, "countrycodes": "se", "format": "jsonv2", "limit": 6})
    except Exception:
        raise HTTPException(502, "place search unavailable")
    return [{"name": r.get("display_name", "")[:120], "type": r.get("type"), "lat": float(r["lat"]), "lon": float(r["lon"]),
             "bbox": [float(r["boundingbox"][2]), float(r["boundingbox"][0]), float(r["boundingbox"][3]), float(r["boundingbox"][1])]}
            for r in res]


_scan = {"status": "idle", "lines": [], "added": 0, "error": None, "scope": None}


def _run_scan(lannr: str | None, start: str, end: str) -> None:
    from datetime import date, timedelta
    from fw import skogs as sk
    from scripts import build_web_data as bwd
    try:
        end_x = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        where = (f"Avverktyp='{sk.REGEN}' AND Inkomdatum >= DATE '{start}' AND Inkomdatum < DATE '{end_x}'"
                 + (f" AND Lannr='{lannr}'" if lannr else ""))
        feats = sk.query_features(config.NOTIFICATIONS_URL, where, "Beteckn,Lannr", "OBJECTID ASC")
        items = [{"beteckn": f["properties"]["Beteckn"], "lannr": f["properties"]["Lannr"]} for f in feats if f.get("geometry")]
        _scan["lines"].append(f"{len(items)} regeneration notifications found; building evidence profiles (no LLM)…")
        def progress(i, b, p):
            if i % 5 == 0 or i == 1:
                _scan["lines"].append(f"{i}: {b} → {p.replace('_', '-').lower()} (rule)")
        new = bwd.add_rule_sites(items, progress)
        _scan.update(status="done", added=len(new))
        _scan["lines"].append(f"Done: {len(new)} new sites added; {len(items) - len(new)} were already in the data.")
    except Exception as e:
        _scan.update(status="error", error=f"{type(e).__name__}: {e}"[:300])


@app.post("/api/scan")
def scan(body: dict = Body(...)):
    """Fetch and profile notifications for one county (or all of Sweden) and a date range; merges into the data."""
    lannr = body.get("lannr") or None
    start, end = str(body.get("from", "")), str(body.get("to", ""))
    if not (len(start) == 10 and len(end) == 10):
        raise HTTPException(400, "from and to must be YYYY-MM-DD")
    with _lock:
        if _scan["status"] == "running":
            raise HTTPException(409, "a scan is already running")
        _scan.update(status="running", lines=[], added=0, error=None, scope={"lannr": lannr, "from": start, "to": end})
    threading.Thread(target=_run_scan, args=(lannr, start, end), daemon=True).start()
    return {"started": _scan["scope"]}


@app.get("/api/scan/status")
def scan_status():
    return _scan


@app.get("/api/live/status")
def live_status():
    return _live


@app.get("/tiles/{src}/{z}/{x}/{y}")
def tile(src: str, z: int, x: int, y: int):
    """Same-origin basemap tiles, cached to disk: a rehearsed view never depends on the tile server again."""
    if src not in TILE_SRC:
        raise HTTPException(404)
    ext = "jpg" if src == "eox" else "png"
    p = config.CACHE_DIR / "tiles" / src / str(z) / str(x) / f"{y}.{ext}"
    if not p.exists():
        try:
            r = _http.get(TILE_SRC[src].format(z=z, x=x, y=y))
        except httpx.HTTPError:
            raise HTTPException(502, "tile server unreachable")
        if r.status_code != 200:
            raise HTTPException(502, f"tile server returned {r.status_code}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
    return FileResponse(p, media_type=f"image/{'jpeg' if ext == 'jpg' else 'png'}",
                        headers={"Cache-Control": "max-age=604800"})


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


app.mount("/", StaticFiles(directory=ROOT / "web"), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
