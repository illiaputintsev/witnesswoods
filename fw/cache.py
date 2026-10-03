"""Disk cache for every HTTP GET: sha256(full URL) -> JSON file.

Reruns are instant and the demo does not depend on Wi-Fi.
"""
import hashlib
import json
import time
from urllib.parse import urlencode

import httpx

from fw.config import CACHE_DIR, USER_AGENT

_client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60.0, follow_redirects=True)


def full_url(url: str, params: dict | None = None) -> str:
    if not params:
        return url
    return f"{url}?{urlencode(params, doseq=True)}"


def _path(url: str):
    return CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


def get_json(url: str, params: dict | None = None, refresh: bool = False,
             slim=None, slim_tag: str = "") -> dict:
    """GET a JSON resource, served from disk if cached. Errors are never cached.

    `slim` optionally shrinks the body before it is stored (e.g. keep only the
    GBIF fields we use); `slim_tag` names that shape so it gets its own cache entry.
    """
    u = full_url(url, params)
    p = _path(u + (f"#{slim_tag}" if slim_tag else ""))
    if p.exists() and not refresh:
        return json.loads(p.read_text())["body"]
    for attempt in range(7):
        try:
            r = _client.get(u)
            r.raise_for_status()
            body = r.json()
            break
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            if attempt == 6:
                raise
            # Rate limits (429) and server errors: honour Retry-After, else back off 2, 4, 8 ... 60 s
            ra = getattr(getattr(e, "response", None), "headers", {}).get("retry-after")
            time.sleep(float(ra) if ra and ra.isdigit() else min(60, 2 ** (attempt + 1)))
    # ArcGIS reports errors with HTTP 200 and an "error" key; do not cache those
    if isinstance(body, dict) and "error" in body:
        raise RuntimeError(f"API error for {u}: {body['error']}")
    if slim:
        body = slim(body)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"url": u, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "body": body}))
    return body


def download_file(url: str, dest) -> None:
    """Download a binary file once; skip if it already exists."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _client.stream("GET", url) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
