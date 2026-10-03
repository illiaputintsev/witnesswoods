"""M4 acceptance helper: screenshots at two frame sizes, tile-blocked fallback, console errors, replay timing.

Needs the server running (python server.py) and Playwright (pip install playwright); uses the installed Chrome.
Run: .venv/bin/python scripts/ui_check.py [--replay-timing]
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "screens"
BASE = "http://127.0.0.1:8000/"
READY = "() => typeof S !== 'undefined' && S.mapReady && S.replay && S.map.loaded()"
TILES = "() => typeof S !== 'undefined' && S.map && S.map.areTilesLoaded()"


def wait_ready(page, settle=1.5):
    page.wait_for_function(READY, timeout=30000)
    try:
        page.wait_for_function(TILES, timeout=15000)
    except Exception:
        pass
    time.sleep(settle)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    week = json.loads((ROOT / "web" / "data" / "week.json").read_text())
    def n_photos(b):
        f = ROOT / "web" / "data" / "sites" / f"{b.replace(' ', '_')}.json"
        d = json.loads(f.read_text()) if f.exists() else {}
        return sum(1 for x in d.get("species_lines", []) if x.get("photo")) if d.get("kind", "agent") == "agent" else -1
    highs = [s["beteckn"] for s in week["sites"] if s["priority"] == "HIGH"]
    high = max(highs, key=n_photos).replace(" ", "_")  # the HIGH agent dossier with the most species photos
    report = {"high_site": high, "views": [], "console_errors": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--use-angle=metal", "--enable-gpu"])
        for w, h in ((1440, 900), (1280, 720)):
            for name, frag, settle in (("landing", "", 1.5), ("replay", "#replay=150", 2.0),
                                       (f"dossier_high", f"#site={high}", 4.0), ("evidence", "#tab=evidence", 1.0),
                                       ("condense", "#tab=condense", 1.0)):
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
                page = ctx.new_page()
                errs = []
                page.on("console", lambda m, errs=errs: m.type == "error" and errs.append(m.text))
                page.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
                page.goto(BASE + f"?v={w}{frag and '-' + name}" + frag)
                wait_ready(page, settle)
                f = OUT / f"{name}_{w}x{h}.png"
                page.screenshot(path=str(f))
                report["views"].append(str(f.relative_to(ROOT)))
                report["console_errors"][f"{name}_{w}"] = errs
                ctx.close()

        # Tiles blocked at the network level: EOX and OSM both unreachable
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.route("**/tiles/**", lambda r: r.abort())  # all basemap tiles unreachable
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE + "?blocked=1")
        page.wait_for_function(READY, timeout=30000)
        page.wait_for_function("() => !document.getElementById('tile-note').hidden", timeout=20000)
        time.sleep(1)
        page.screenshot(path=str(OUT / "tiles_blocked_1440x900.png"))
        report["tiles_blocked"] = {"fallback_note_visible": True, "sites_drawn": page.evaluate("S.sites.length"),
                                   "page_errors": errs, "screenshot": "out/screens/tiles_blocked_1440x900.png"}
        ctx.close()

        if "--replay-timing" in sys.argv:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.goto(BASE + "?timing=1")
            wait_ready(page)
            page.evaluate("""() => { window.__frames = []; let last = performance.now();
                const f = (t) => { window.__frames.push(t - last); last = t; if (!S.finished) requestAnimationFrame(f); };
                requestAnimationFrame(f); window.__t0 = performance.now(); toggleReplay(); }""")
            page.wait_for_function("() => S.finished", timeout=240000, polling=500)
            res = page.evaluate("""() => { const fr = window.__frames; const s = [...fr].sort((a,b)=>a-b);
                return { seconds: (performance.now() - window.__t0) / 1000, steps: S.replay.steps.length,
                         frames: fr.length, median_ms: s[Math.floor(s.length/2)], p95_ms: s[Math.floor(s.length*0.95)],
                         over_50ms: fr.filter(x => x > 50).length, over_100ms: fr.filter(x => x > 100).length }; }""")
            report["replay_timing_headless"] = res
            ctx.close()
        browser.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
