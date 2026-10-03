"""Agent-facing tools: JSON schemas plus the functions behind them.

Every fact a tool returns is stored losslessly in the evidence store and cited by ID.
The key-habitat layer is deliberately absent: it lives only in fw/validate.py.
"""
import json
import re
import time
from functools import lru_cache

from fw import config, evidence, gbif, profile, redlist, rubric

PRIORITIES = ("HIGH", "MEDIUM", "LOW", "UNDER_SURVEYED", "ALREADY_FELLED")
BUFFERS = (0, 100, 250, 500, 1000)
DOSSIERS = config.OUT / "dossiers"

# Affirmative phrases that break the honesty rules (BRIEF section 2). record_finding rejects them in
# reasons, contradictions, next_action and override text. Uncertainties and not_established are not
# scanned, because they exist to hold negations ("this does not show the site is safe to fell").
BANNED = (
    r"safe to (clear-?)?(fell|cut|harvest|log)", r"safely (fell|felled|cut|harvest)", r"safe for (felling|harvest|logging)",
    r"(fine|ok|okay|suitable) (to|for) (fell|cut|harvest|felling)", r"felling can (safely )?proceed",
    r"(poses|has|with|carries) no (ecological )?risk", r"(minimal|low|no) (ecological )?risk (to|from|of|for)",
    r"no biodiversity (value|interest)", r"\b(high|low|rich|poor) biodiversity\b", r"biodiversity is (low|absent|high)",
    r"species (are|is) absent", r"no (\w+[- ])*species (are |is )?(present|occur)",
    r"legally protected", r"protected (by|under) (\w+ )?law",
)
NEGATION = re.compile(r"\b(not|never|no evidence|n't|whether|does not mean|cannot)\b[^.;]{0,40}$", re.IGNORECASE)


@lru_cache(maxsize=64)
def _profile(beteckn: str) -> dict:
    return profile.build(beteckn)


def _sp_brief(sp: dict) -> dict:
    return {
        "name": sp["scientific_name"], "swedish": sp["swedish_name"], "group": sp["group"],
        "category": sp["category"], "records": sp["records"], "latest_year": sp["latest_year"],
        "nearest_record": sp["nearest_record"],
        "forest_associated": sp["forest_associated"], "felling_relevant": sp["felling_relevant"],
        "felling_factors": sp["felling_factors"], "mobility": sp["mobility"],
        "eligible": rubric._eligible(sp), "evidence_id": sp["evidence_id"],
    }


# ---------------------------------------------------------------- tool functions

def get_notification(beteckn: str) -> dict:
    p = _profile(beteckn)
    n = p["notification"]
    return {
        "beteckn": n["beteckn"], "kommun": n["kommun"], "lan": n["lan"], "avverktyp": n["avverktyp"],
        "skogstyp": n["skogstyp"], "inkomdatum": n["inkomdatum"], "anmald_ha": n["anmald_ha"],
        "polygon_ha": n["polygon_ha"], "status": n["status"], "natfor_ha": n["natfor_ha"],
        "centroid_lonlat": n["centroid"], "site_equivalent_radius_m": p["site_radius_m"],
        "date_matches_id_year": n["date_year_ok"], "evidence_id": n["evidence_id"],
    }


def check_completed_felling(beteckn: str) -> dict:
    f = _profile(beteckn)["felling"]
    return {"overlap_ha": f["overlap_ha"], "overlap_pct": f["overlap_pct"], "fellings": f["fellings"][:10],
            "source": "Skogsstyrelsen completed fellings (satellite change detection)", "evidence_id": f["evidence_id"]}


def query_species(beteckn: str, buffer_m: int = 0) -> dict:
    if buffer_m not in BUFFERS:
        raise ValueError(f"buffer_m must be one of {BUFFERS}")
    p = _profile(beteckn)
    ring = p["rings"][buffer_m]
    red = sorted(ring["redlisted"].values(),
                 key=lambda s: ({"CR": 0, "EN": 1, "VU": 2, "NT": 3}.get(s["category"], 9), s["min_distance_m"]))
    shown = [s for s in red if s["category"] in ("CR", "EN", "VU", "NT")]
    return {
        "evidence_id": ring["evidence_id"], "buffer_m": buffer_m, "since_year": profile.SINCE_YEAR,
        "uncertainty_allowance_m": ring["allowance_m"],
        "records_in_buffer": ring["records"], "records_kept": ring["kept"],
        "records_dropped_for_uncertainty": ring["dropped_uncertainty"] + ring["dropped_no_uncertainty"],
        "distinct_species": ring["species"], "observation_days": ring["days"],
        "counts": {"threatened": ring["threatened"], "threatened_forest": ring["threatened_forest"],
                   "threatened_forest_felling": ring["threatened_forest_felling"], "eligible": ring["eligible"],
                   "site_bound_forest_nt": ring["nt_eligible"], "mobile_supporting": ring["mobile_supporting"]},
        "redlisted": [_sp_brief(s) for s in shown[:25]],
        "redlisted_not_shown": max(0, len(shown) - 25),
        "dd_re_excluded": ring["excluded_dd_re"],
        "top_records": _top_records(p, buffer_m),
        "rubric_hint": rubric.hint(p),
        "notes": "A record shows a species was recorded, not that it occupies the site today. "
                 "Categories describe extinction risk, not legal protection.",
    }


def _top_records(p: dict, buffer_m: int, n: int = 15) -> list[dict]:
    """Nearest red-listed records in this ring, each with its own distance, uncertainty and year."""
    out = []
    for sp in p["rings"][buffer_m]["redlisted"].values():
        for x in sp["recs"]:
            out.append({**x, "species": sp["scientific_name"], "category": sp["category"],
                        "url": gbif.record_url(x["key"])})
    out.sort(key=lambda r: (r["dist_m"], r["unc_m"]))
    return out[:n]


def observation_effort(beteckn: str) -> dict:
    p = _profile(beteckn)
    e = p["effort"]
    return {
        "definition": e["definition"], "records": e["records"], "distinct_species": e["species"],
        "observation_days": e["days"], "county_median_records": e["county_median"],
        "county_p25_records": e["county_p25"], "percentile_in_county_sample": e["percentile"],
        "level": e["level"], "reference": e["reference"], "evidence_id": e["evidence_id"],
        "rubric_hint": rubric.hint(p),
        "notes": "Low effort means few people have recorded here; it says nothing about what lives here.",
    }


def redlist_lookup(scientific_name: str) -> dict:
    entry = redlist.lookup(scientific_name)
    if entry is None:
        return {"found": False, "scientific_name": scientific_name,
                "notes": "Not on the Swedish Red List 2025 (or the name did not match)."}
    eid = evidence.add("redlist", "redlist_entry", entry, "SLU Artdatabanken, Swedish Red List 2025 (researchdata.se 2026-63)")
    return {"found": True, **entry, "evidence_id": eid}


def get_evidence(evidence_ids: list[str]) -> dict:
    ids = list(dict.fromkeys(evidence_ids))[:20]
    items = evidence.get(ids)
    return {"items": items, "missing": sorted(set(ids) - {i["id"] for i in items}),
            "truncated": len(dict.fromkeys(evidence_ids)) > 20}


# ---------------------------------------------------------------- record_finding

def _check_cited(items: list, label: str, valid: dict, errors: list) -> None:
    for i, it in enumerate(items or []):
        ids = it.get("evidence_ids") or []
        if not ids:
            errors.append(f"{label}[{i}] has no evidence_ids")
        bad = [x for x in ids if x not in valid]
        if bad:
            errors.append(f"{label}[{i}] cites unknown or other-site evidence IDs {bad}")


def _banned(texts: list[str]) -> list[str]:
    """Affirmative matches only: a match preceded (same clause, ~40 chars) by a negation is allowed."""
    hits = []
    for t in texts:
        for pat in BANNED:
            for m in re.finditer(pat, t or "", re.IGNORECASE):
                if not NEGATION.search(t[:m.start()]):
                    hits.append(m.group(0))
    return sorted(set(hits))


def record_finding(beteckn: str, priority: str, reasons: list, next_action: str, contradictions: list | None = None,
                   uncertainties: list | None = None, not_established: list | None = None,
                   override_reason: dict | None = None, _meta: dict | None = None) -> dict:
    p = _profile(beteckn)
    errors = []
    if priority not in PRIORITIES:
        errors.append(f"priority must be one of {PRIORITIES}")
    if not reasons:
        errors.append("at least one reason is required")
    for label, items in (("reasons", reasons or []), ("contradictions", contradictions or [])):
        if not isinstance(items, list) or not all(isinstance(it, dict) and isinstance(it.get("claim"), str)
                                                  and it["claim"].strip() for it in items):
            errors.append(f"{label} must be a list of {{claim, evidence_ids}} with non-empty claims")
            return {"ok": False, "errors": errors}
    if not not_established or not all(isinstance(x, str) and x.strip() for x in not_established):
        errors.append("not_established must list at least one thing this evidence does not establish")
    if not uncertainties or not all(isinstance(x, str) and x.strip() for x in uncertainties):
        errors.append("uncertainties must list at least one uncertainty")
    if not isinstance(next_action, str) or not next_action.strip():
        errors.append("next_action is required")
    # Hard rubric limits that no override can cross
    if priority == "LOW" and p["effort"]["records"] < p["effort"]["county_median"]:
        errors.append("LOW requires effort at or above the county median; use UNDER_SURVEYED")
    if priority == "ALREADY_FELLED" and p["felling"]["overlap_pct"] < rubric.FELLED_PCT:
        errors.append(f"ALREADY_FELLED requires completed felling over >= {rubric.FELLED_PCT}% of the polygon")
    cited = {x for group in (reasons or [], contradictions or [], [override_reason] if override_reason else [])
             for it in group for x in (it.get("evidence_ids") or [])}
    valid = {e["id"]: e for e in evidence.get(sorted(cited))
             if e["notification"] in (beteckn, "redlist")}
    _check_cited(reasons, "reasons", valid, errors)
    _check_cited(contradictions, "contradictions", valid, errors)
    hint = rubric.hint(p)
    if priority != hint["priority"]:
        if not override_reason or not override_reason.get("text"):
            errors.append(f"priority {priority} differs from rubric_hint {hint['priority']}: "
                          "pass override_reason {text, evidence_ids}")
        else:
            _check_cited([override_reason], "override_reason", valid, errors)
    texts = [r.get("claim", "") for r in reasons or []] + [c.get("claim", "") for c in contradictions or []]
    texts += [next_action or ""]
    texts += [override_reason.get("text", "")] if override_reason else []
    hits = _banned(texts)
    if hits:
        errors.append(f"text breaks the honesty rules (matched {hits}); rephrase without these claims")
    if errors:
        return {"ok": False, "errors": errors}

    n = p["notification"]
    dossier = {
        "beteckn": beteckn, "priority": priority, "rubric_hint": hint,
        "override_reason": override_reason if priority != hint["priority"] else None,
        "reasons": reasons, "contradictions": contradictions or [], "uncertainties": uncertainties or [],
        "not_established": not_established or [], "next_action": next_action,
        "notification": {k: n[k] for k in ("kommun", "lan", "avverktyp", "skogstyp", "inkomdatum",
                                           "anmald_ha", "polygon_ha", "status", "centroid")},
        "counts": _rank_counts(p), "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **(_meta or {}),
    }
    hint_ev = {e["id"]: e for e in evidence.get(hint["evidence_ids"])}
    md = render_md(dossier, {**hint_ev, **valid})  # render first, so a failure writes nothing
    DOSSIERS.mkdir(parents=True, exist_ok=True)
    stem = beteckn.replace(" ", "_")
    (DOSSIERS / f"{stem}.json").write_text(json.dumps(dossier, indent=1, ensure_ascii=False))
    (DOSSIERS / f"{stem}.md").write_text(md)
    return {"ok": True, "dossier": f"out/dossiers/{stem}.json", "priority": priority}


def _rank_counts(p: dict) -> dict:
    """Snapshot used by fw/rank.py: eligible species within 1000 m by category, and red-listed records."""
    wide = p["rings"][1000]["redlisted"].values()
    elig = [s for s in wide if rubric._eligible(s)]
    return {"eligible_cr_en": sum(s["category"] in ("CR", "EN") for s in elig),
            "eligible_vu": sum(s["category"] == "VU" for s in elig),
            "nt_eligible": sum(rubric._nt_eligible(s) for s in wide),
            "redlisted_records": sum(s["records"] for s in wide if s["category"] not in redlist.EXCLUDED)}


def render_md(d: dict, ev: dict) -> str:
    def cite(ids):
        out = []
        for i in ids:
            urls = (ev.get(i, {}).get("payload", {}) or {}).get("record_urls") or []  # nearest qualifying first
            out.append(f"{i}" + (f" ([GBIF]({urls[0]}))" if urls else ""))
        return ", ".join(out)

    n = d["notification"]
    L = [f"# {d['beteckn']}: {d['priority']}", "",
         f"{n['kommun']}, {n['lan']} | {n['avverktyp']} | received {n['inkomdatum']} | {n['polygon_ha']} ha",
         "", f"Rubric hint: **{d['rubric_hint']['priority']}** ({d['rubric_hint']['rule']}) "
             f"[{cite(d['rubric_hint']['evidence_ids'])}]"]
    if d.get("override_reason"):
        o = d["override_reason"]
        L += [f"Agent override: {o['text']} [{cite(o.get('evidence_ids', []))}]"]
    L += ["", "## Why"] + [f"- {r['claim']} [{cite(r['evidence_ids'])}]" for r in d["reasons"]]
    L += ["", "## Contradictions"] + ([f"- {c['claim']} [{cite(c['evidence_ids'])}]" for c in d["contradictions"]]
                                     or ["- None found."])
    L += ["", "## Uncertainties"] + ([f"- {u}" for u in d["uncertainties"]] or ["- None stated."])
    L += ["", "## What this evidence does not establish"] + [f"- {x}" for x in d["not_established"]]
    L += ["", "## Next action", d["next_action"], "",
          "---", "Triage for human review only. This is not a biodiversity measurement and not a judgement on "
          "whether felling is legal or appropriate. Sources: Skogsstyrelsen (CC0), Artportalen via GBIF (CC0), "
          "SLU Swedish Red List 2025 (CC0)."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- schemas

_CITED = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["claim", "evidence_ids"],
          "properties": {"claim": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}}
_B = {"type": "string", "description": "Notification ID, e.g. 'A 41029-2026'."}

SCHEMAS = [
    {"name": "get_notification",
     "description": "Read a felling notification: municipality, felling type, forest type, date received, notified and "
                    "polygon hectares, status, centroid. Returns an evidence_id.",
     "input_schema": {"type": "object", "properties": {"beteckn": _B}, "required": ["beteckn"]}},
    {"name": "check_completed_felling",
     "description": "Overlap between the notification polygon and Skogsstyrelsen's completed-fellings layer (satellite "
                    "change detection): overlap hectares and percent, felling dates. Returns an evidence_id.",
     "input_schema": {"type": "object", "properties": {"beteckn": _B}, "required": ["beteckn"]}},
    {"name": "query_species",
     "description": "Artportalen species records (via GBIF, since 2016) within buffer_m of the polygon edge "
                    "(0 = inside). A record counts only if its coordinate uncertainty is no larger than the buffer "
                    "(inside: the site's equivalent radius). Returns record counts, records dropped for uncertainty, "
                    "red-listed species with Swedish Red List 2025 category, forest/felling/mobility flags and "
                    "evidence_ids, top records with GBIF links, and the deterministic rubric_hint.",
     "input_schema": {"type": "object", "properties": {"beteckn": _B, "buffer_m": {"type": "integer", "enum": list(BUFFERS),
                      "description": "Distance from the polygon edge in metres."}}, "required": ["beteckn", "buffer_m"]}},
    {"name": "observation_effort",
     "description": "How much recording has happened near the site: Artportalen records, distinct species and "
                    "observation days since 2016 within 1000 m of the polygon, compared with a county reference "
                    "sample. Use it before treating few records as meaningful. Returns an evidence_id and the "
                    "rubric_hint.",
     "input_schema": {"type": "object", "properties": {"beteckn": _B}, "required": ["beteckn"]}},
    {"name": "redlist_lookup",
     "description": "Full Swedish Red List 2025 entry for one species: category, criteria, landscapes, forest "
                    "habitats, negative impact factors, dead-wood dependence. Returns an evidence_id.",
     "input_schema": {"type": "object", "properties": {"scientific_name": {"type": "string"}},
                      "required": ["scientific_name"]}},
    {"name": "get_evidence",
     "description": "Retrieve stored evidence payloads by ID (lossless). Use when you need exact facts again.",
     "input_schema": {"type": "object", "properties": {"evidence_ids": {"type": "array", "items": {"type": "string"},
                      "maxItems": 20}}, "required": ["evidence_ids"]}},
    {"name": "record_finding",
     "description": "Write the dossier for one notification. Call exactly once per notification, last. Every reason and "
                    "contradiction must cite evidence IDs from this notification's tool results. If priority differs "
                    "from the rubric_hint, override_reason is required. The call is rejected with errors if a rule "
                    "is broken; fix and call again.",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["beteckn", "priority", "reasons", "uncertainties", "not_established", "next_action"],
                      "properties": {
                          "beteckn": _B,
                          "priority": {"type": "string", "enum": list(PRIORITIES)},
                          "reasons": {**_CITED, "description": "Why this priority; each claim with evidence IDs."},
                          "contradictions": {**_CITED, "description": "Where sources disagree; each with evidence IDs."},
                          "uncertainties": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                          "not_established": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                              "description": "What this evidence does not establish."},
                          "next_action": {"type": "string", "description": "Concrete next step for a human reviewer."},
                          "override_reason": {"type": "object", "additionalProperties": False,
                                              "required": ["text", "evidence_ids"],
                                              "properties": {"text": {"type": "string"},
                                                             "evidence_ids": {"type": "array", "items": {"type": "string"}}},
                                              "description": "Required only when priority differs from rubric_hint."}}}},
]

FUNCS = {"get_notification": get_notification, "check_completed_felling": check_completed_felling,
         "query_species": query_species, "observation_effort": observation_effort,
         "redlist_lookup": redlist_lookup, "get_evidence": get_evidence, "record_finding": record_finding}
