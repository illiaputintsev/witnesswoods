"""Lossless SQLite evidence store. Every fact a tool returns gets an ID like E-0001.

Adding the same (notification, kind, payload) twice returns the existing ID, so
reruns over the disk cache do not pile up duplicates.
"""
import hashlib
import json
import sqlite3
import time
from functools import lru_cache

from fw.config import OUT

DB = OUT / "evidence.db"


@lru_cache(maxsize=1)
def _conn():
    OUT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS evidence(
        id TEXT PRIMARY KEY, notification TEXT, kind TEXT, payload TEXT,
        source_url TEXT, created_at TEXT, digest TEXT UNIQUE)""")
    return c


def add(notification: str, kind: str, payload: dict, source_url: str = "") -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(f"{notification}|{kind}|{body}".encode()).hexdigest()
    c = _conn()
    row = c.execute("SELECT id FROM evidence WHERE digest=?", (digest,)).fetchone()
    if row:
        return row[0]
    n = c.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    eid = f"E-{n + 1:04d}"
    c.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
              (eid, notification, kind, body, source_url, time.strftime("%Y-%m-%dT%H:%M:%S"), digest))
    c.commit()
    return eid


def get(ids: list[str]) -> list[dict]:
    q = f"SELECT id, notification, kind, payload, source_url, created_at FROM evidence WHERE id IN ({','.join('?' * len(ids))})"
    rows = _conn().execute(q, ids).fetchall()
    return [{"id": r[0], "notification": r[1], "kind": r[2], "payload": json.loads(r[3]),
             "source_url": r[4], "created_at": r[5]} for r in rows]


def exists(ids: list[str]) -> set[str]:
    return {e["id"] for e in get(ids)} if ids else set()
