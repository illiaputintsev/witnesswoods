"""Deterministic rubric hint: prompts/rubric.md v1 applied mechanically to an evidence profile.

The agent sees this hint and may override it only with a reason citing evidence IDs.
"""
from fw import redlist

FELLED_PCT = 50
HIGH_RING = 250
NT_RING = 250
NT_MIN = 3
WIDE_RING = 1000


def _eligible(sp: dict) -> bool:
    return (sp["category"] in redlist.THREATENED and sp["forest_associated"]
            and sp["felling_relevant"] and sp["mobility"] == "site_bound")


def _nt_eligible(sp: dict) -> bool:
    return (sp["category"] == "NT" and sp["forest_associated"]
            and sp["felling_relevant"] and sp["mobility"] == "site_bound")


def _ids(species: list[dict]) -> list[str]:
    return sorted({s["evidence_id"] for s in species})


def hint(profile: dict) -> dict:
    rings, eff, fell = profile["rings"], profile["effort"], profile["felling"]
    near = [s for s in rings[HIGH_RING]["redlisted"].values() if _eligible(s)]
    wide = [s for s in rings[WIDE_RING]["redlisted"].values() if _eligible(s)]
    nt = [s for s in rings[NT_RING]["redlisted"].values() if _nt_eligible(s)]
    threatened_any = [s for s in rings[WIDE_RING]["redlisted"].values() if s["category"] in redlist.THREATENED]
    effort_ok = eff["records"] >= eff["county_median"]
    r_near, r_nt, r_wide = rings[HIGH_RING]["evidence_id"], rings[NT_RING]["evidence_id"], rings[WIDE_RING]["evidence_id"]

    def out(priority, rule, ids):
        return {"priority": priority, "rule": rule, "evidence_ids": sorted(set(ids)),
                "basis": "prompts/rubric.md v1 applied to all rings 0-1000 m and effort; a default, not a verdict"}

    if fell["overlap_pct"] >= FELLED_PCT:
        return out("ALREADY_FELLED", f"completed felling covers {fell['overlap_pct']}% (>= {FELLED_PCT}%)",
                   [fell["evidence_id"]])
    if near:
        return out("HIGH", f"{len(near)} eligible species recorded within {HIGH_RING} m", [r_near, *_ids(near)])
    if wide:
        return out("MEDIUM", f"{len(wide)} eligible species recorded beyond {HIGH_RING} m but within {WIDE_RING} m, "
                             f"none within {HIGH_RING} m", [r_near, r_wide, *_ids(wide)])
    if len(nt) >= NT_MIN:
        return out("MEDIUM", f"{len(nt)} site-bound forest NT species recorded within {NT_RING} m", [r_nt, *_ids(nt)])
    if not effort_ok and threatened_any:
        return out("MEDIUM", f"contradiction: effort {eff['records']} below county median {eff['county_median']} "
                             f"while {len(threatened_any)} threatened species are recorded within {WIDE_RING} m",
                   [eff["evidence_id"], r_wide, *_ids(threatened_any)])
    if effort_ok:
        return out("LOW", f"effort {eff['records']} >= county median {eff['county_median']}; no eligible species "
                          f"recorded within {WIDE_RING} m; {len(nt)} site-bound forest NT species recorded within "
                          f"{NT_RING} m", [eff["evidence_id"], r_wide, r_nt])
    return out("UNDER_SURVEYED", f"effort {eff['records']} below county median {eff['county_median']}; "
                                 f"no eligible species recorded within {WIDE_RING} m", [eff["evidence_id"], r_wide])
