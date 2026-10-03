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
from fastapi import FastAPI, HTTPException
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
