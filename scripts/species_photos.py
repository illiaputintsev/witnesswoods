"""Reference photos for every species cited in a dossier, stored locally so the demo works offline.

Source order: iNaturalist taxa API (default_photo, medium; skipped when it has no licence), then the
Wikidata P18 image on Wikimedia Commons. Writes web/data/img/<slug>.jpg and web/data/photos.json.
These are reference photos of the species, never photos from the site.

Run: .venv/bin/python scripts/species_photos.py
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw import config  # noqa: E402
from fw.cache import _client, download_file, get_json  # noqa: E402

WEB = config.ROOT / "web" / "data"
IMG = WEB / "img"
OUT = WEB / "photos.json"
# Wikimedia's robot policy asks for a User-Agent that identifies the project
WM_HEADERS = {"User-Agent": "witnesswoods-hackathon/0.1 (https://github.com/illiaputintsev/witnesswoods)"}
LICENCE = {"cc0": "CC0", "cc-by": "CC BY", "cc-by-nc": "CC BY-NC", "cc-by-sa": "CC BY-SA", "cc-by-nd": "CC BY-ND",
           "cc-by-nc-sa": "CC BY-NC-SA", "cc-by-nc-nd": "CC BY-NC-ND"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def polite(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    """Cached GET; sleep only when the response was not cached yet (about 1 request per second)."""
    from fw.cache import _path, full_url
    cached = _path(full_url(url, params)).exists()
    body = get_json(url, params, headers=headers)
    if not cached:
        time.sleep(1.0)
    return body


def inaturalist(name: str) -> dict | None:
    body = polite("https://api.inaturalist.org/v1/taxa", {"q": name, "rank": "species,subspecies", "per_page": 10})
    for t in body.get("results", []):
        if t.get("name", "").lower() != name.lower():
            continue
        ph = t.get("default_photo") or {}
        code = ph.get("license_code")
        if not code or not ph.get("medium_url"):
            return None  # no licence: skip, try Commons
        return {"url": ph["medium_url"], "author": ph.get("attribution", ""), "licence": LICENCE.get(code, code.upper()),
                "source": "iNaturalist", "source_url": f"https://www.inaturalist.org/taxa/{t['id']}"}
    return None


def commons(name: str) -> dict | None:
    q = f'SELECT ?img WHERE {{ ?item wdt:P225 "{name}"; wdt:P18 ?img }} LIMIT 1'
    body = polite("https://query.wikidata.org/sparql", {"query": q, "format": "json"}, WM_HEADERS)
    rows = body.get("results", {}).get("bindings", [])
    if not rows:
        return None
    fname = rows[0]["img"]["value"].rsplit("/", 1)[-1]
    from urllib.parse import unquote
    title = "File:" + unquote(fname)
    info = polite("https://commons.wikimedia.org/w/api.php", {"action": "query", "titles": title, "prop": "imageinfo",
                                                              "iiprop": "extmetadata|url", "iiurlwidth": 400, "format": "json"},
                  WM_HEADERS)
    page = next(iter(info.get("query", {}).get("pages", {}).values()), {})
    ii = (page.get("imageinfo") or [{}])[0]
    meta = ii.get("extmetadata", {})
    lic = re.sub("<[^>]+>", "", meta.get("LicenseShortName", {}).get("value", "")).strip()
    if not lic or not ii.get("thumburl"):
        return None
    artist = re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip() or "unknown author"
    return {"url": ii["thumburl"], "author": f"© {artist}", "licence": lic, "source": "Wikimedia Commons",
            "source_url": ii.get("descriptionurl", "https://commons.wikimedia.org/wiki/" + quote(title))}


def cited_species() -> dict:
    names = {}
    for f in (WEB / "sites").glob("*.json"):
        d = json.loads(f.read_text())
        if d.get("kind") == "rule":
            continue  # photos are fetched for agent dossiers; rule-only profiles reuse them where available
        for s in d.get("species_lines", []):
            names[s["scientific"]] = s.get("swedish")
    return names


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    have = json.loads(OUT.read_text()) if OUT.exists() else {}
    names = cited_species()
    print(f"{len(names)} cited species, {len(have)} already have a photo entry")
    for i, (name, sw) in enumerate(sorted(names.items()), 1):
        if name in have:
            continue
        try:
            ph = inaturalist(name) or commons(name)
        except Exception as e:  # network trouble: leave this species without a photo
            print("  skip", name, type(e).__name__)
            continue
        if not ph:
            have[name] = None
            continue
        ext = ".jpg" if ".jp" in ph["url"].lower() else ".png" if ".png" in ph["url"].lower() else ".jpg"
        dest = IMG / f"{slug(name)}{ext}"
        try:
            download_file(ph["url"], dest, WM_HEADERS if ph["source"] == "Wikimedia Commons" else None)
        except Exception as e:
            print("  image failed", name, type(e).__name__)
            continue
        have[name] = {**ph, "file": f"img/{dest.name}", "swedish": sw,
                      "caption": f"Reference photo of the species, not from this site. {ph['author']}, {ph['licence']}, {ph['source']}."}
        if i % 20 == 0:
            print(f"  {i}/{len(names)}", flush=True)
            OUT.write_text(json.dumps(have, ensure_ascii=False, indent=1))
    OUT.write_text(json.dumps(have, ensure_ascii=False, indent=1))
    got = sum(1 for v in have.values() if v)
    src = {}
    for v in have.values():
        if v:
            src[v["source"]] = src.get(v["source"], 0) + 1
    print(f"photos: {got}/{len(have)} species ({src}); none found: {[k for k, v in have.items() if not v][:10]}")


if __name__ == "__main__":
    main()
