"""Transparent ordering of dossiers (BRIEF 4.4). No invented weights: a plain sort on five keys.

1. priority (HIGH > MEDIUM > UNDER_SURVEYED > LOW > ALREADY_FELLED)
2. eligible CR/EN species within 1000 m
3. eligible VU species within 1000 m
4. site-bound forest NT species within 1000 m
5. red-listed records within 1000 m
"""
import json

from fw.config import OUT

ORDER = {"HIGH": 0, "MEDIUM": 1, "UNDER_SURVEYED": 2, "LOW": 3, "ALREADY_FELLED": 4}


def key(d: dict):
    c = d["counts"]
    return (ORDER[d["priority"]], -c["eligible_cr_en"], -c["eligible_vu"], -c["nt_eligible"], -c["redlisted_records"])


def load_dossiers(beteckns: list[str] | None = None) -> list[dict]:
    files = sorted((OUT / "dossiers").glob("*.json"))
    ds = [json.loads(f.read_text()) for f in files]
    return [d for d in ds if beteckns is None or d["beteckn"] in beteckns]


def ranked(beteckns: list[str] | None = None) -> list[dict]:
    return sorted(load_dossiers(beteckns), key=key)
