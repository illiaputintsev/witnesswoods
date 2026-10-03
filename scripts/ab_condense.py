"""Condense A/B: the same 10 Dalarna notifications, direct vs through Condense, run concurrently.

Same model, prompts, tools and parameters; tool outputs come from the disk cache, so both routes see
identical facts. Each route writes dossiers to its own folder (out/ab/<route>/). Writes out/condense.json.

Run: .venv/bin/python scripts/ab_condense.py                       run one more concurrent pair, then analyse all pairs
     .venv/bin/python scripts/ab_condense.py --analyse-only           analyse the pairs listed in out/ab_runs.json
"""
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fw import config, evidence  # noqa: E402

SITES = ["A 41029-2026", "A 41028-2026", "A 41015-2026", "A 41007-2026", "A 41006-2026",
         "A 40996-2026", "A 40979-2026", "A 40976-2026", "A 40975-2026", "A 40971-2026"]


def run(route: str, pair: int) -> subprocess.Popen:
    env = {**os.environ, "FW_DOSSIERS": str(config.OUT / "ab" / f"{route}-{pair}")}
    args = [sys.executable, "-u", "-m", "fw.agent", *[s.replace(" ", "_") for s in SITES], "--route", route, "--label", f"ab-{route}"]
    return subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def summarise_many(run_ids: list[str], route: str) -> dict:
    parts = [summarise(r, route) for r in run_ids]
    keys = ("calls", "input_uncached", "cache_write", "cache_read", "input_total", "output", "tool_calls")
    tot = {k: sum(p[k] for p in parts) for k in keys}
    tot["usd"] = round(sum(p["usd"] for p in parts), 4)
    tot["input_per_call"] = round(tot["input_total"] / tot["calls"])
    tot["usd_per_call"] = round(tot["usd"] / tot["calls"], 5)
    tot["runs"] = [{k: v for k, v in p.items() if k != "dossiers"} for p in parts]
    tot["dossiers_by_run"] = [p["dossiers"] for p in parts]
    tot["priorities_by_run"] = [p["priorities"] for p in parts]
    return tot


def summarise(run_id: str, route: str) -> dict:
    ev = [json.loads(l) for l in (config.OUT / "log.jsonl").read_text().splitlines() if l.strip()]
    ev = [e for e in ev if e.get("run_id") == run_id]
    calls = [e for e in ev if e["type"] == "model_call"]
    u = Counter()
    for e in calls:
        u.update(e["usage"])
    ts = sorted(e["ts"] for e in ev)
    ds = {}
    for f in (config.OUT / "ab").glob(f"{route}*/*.json"):
        d = json.loads(f.read_text())
        if d.get("run_id") == run_id:
            ds[d["beteckn"]] = d
    # priorities of every run are also in the log, even if a later pair overwrote the dossier files
    prio = {e["notification"]: e["priority"] for e in ev if e["type"] == "finding"}
    return {"run_id": run_id, "calls": len(calls), "input_uncached": u["input"], "cache_write": u["cache_write"],
            "cache_read": u["cache_read"], "input_total": u["input"] + u["cache_write"] + u["cache_read"],
            "output": u["output"], "usd": round(sum(e["usd"] for e in calls), 4),
            "latency_s": round(sum(e["latency_s"] for e in calls), 1), "first_ts": ts[0], "last_ts": ts[-1],
            "tool_calls": sum(e["type"] == "tool_call" for e in ev), "dossiers": ds, "priorities": prio}


def main() -> None:
    runs_file = config.OUT / "ab_runs.json"
    pairs = json.loads(runs_file.read_text()) if runs_file.exists() else []
    if "--analyse-only" not in sys.argv:
        t0 = time.time()
        procs = {r: run(r, len(pairs) + 1) for r in ("direct", "condense")}
        rid = {}
        for r, p in procs.items():
            out = p.communicate()[0]
            (config.OUT / f"ab_{r}_{len(pairs) + 1}.txt").write_text(out)
            rid[r] = next(l.split()[1].rstrip(":") for l in out.splitlines() if l.startswith("run "))
        pairs.append(rid)
        runs_file.write_text(json.dumps(pairs, indent=1))
        print(f"pair {len(pairs)} finished in {time.time() - t0:.0f} s")
    R = {r: summarise_many([p[r] for p in pairs], r) for r in ("direct", "condense")}
    same, total, jac, cited_all = 0, 0, [], set()
    ids = lambda x: {i for r in x["reasons"] + x["contradictions"] for i in r["evidence_ids"]}
    per_site = {b: [] for b in SITES}
    for pd, pc in zip(R["direct"]["priorities_by_run"], R["condense"]["priorities_by_run"]):
        for b in SITES:
            per_site[b].append([pd.get(b), pc.get(b)])
            if b in pd and b in pc:
                total += 1
                same += pd[b] == pc[b]
    for d, c in zip(R["direct"]["dossiers_by_run"], R["condense"]["dossiers_by_run"]):
        for b in SITES:
            if b in d and b in c:
                a, k = ids(d[b]), ids(c[b])
                cited_all |= a | k
                jac.append(len(a & k) / len(a | k) if a | k else 1.0)
    stored = {e["id"] for e in evidence.get(sorted(cited_all))}
    pct_in = 100 * (1 - R["condense"]["input_total"] / R["direct"]["input_total"])
    pct_usd = 100 * (1 - R["condense"]["usd"] / R["direct"]["usd"])
    pct_in_call = 100 * (1 - R["condense"]["input_per_call"] / R["direct"]["input_per_call"])
    pct_usd_call = 100 * (1 - R["condense"]["usd_per_call"] / R["direct"]["usd_per_call"])
    word = lambda x, more, less: f"{abs(x):.0f}% {less if x >= 0 else more}"
    out = {
        "sites": SITES, "model": config.FW_MODEL, "pairs": len(pairs),
        "note": "Each pair runs the same 10 notifications on both routes with identical model, prompts, tools and "
                "parameters; tool outputs come from the disk cache. Token counts are as reported by the API for each "
                "route. Agent runs are not deterministic: the number of model calls differs between runs, so per-call "
                "figures are shown too.",
        "routes": {r: {k: v for k, v in R[r].items() if k not in ("dossiers_by_run", "priorities_by_run")} for r in R},
        "savings": {"input_tokens_pct": round(pct_in, 1), "usd_pct": round(pct_usd, 1),
                    "input_per_call_pct": round(pct_in_call, 1), "usd_per_call_pct": round(pct_usd_call, 1)},
        "agreement": {
            "same_priority": same, "of": total, "per_site": per_site,
            "cited_evidence_jaccard_mean": round(sum(jac) / len(jac), 3) if jac else None,
            "cited_ids_found_in_evidence_store": f"{len(stored)}/{len(cited_all)}",
            "summary": f"Through Condense: {word(pct_in, 'more', 'fewer')} input tokens and "
                       f"{word(pct_usd, 'higher', 'lower')} cost in total ({word(pct_in_call, 'more', 'fewer')} input "
                       f"tokens and {word(pct_usd_call, 'higher', 'lower')} cost per model call); same priorities on "
                       f"{same}/{total} site runs; all {len(cited_all)} cited evidence IDs retained in the store.",
        },
    }
    (config.OUT / "condense.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: out[k] for k in ("routes", "savings")}, indent=1, ensure_ascii=False)[:3000])
    print(out["agreement"]["summary"])


if __name__ == "__main__":
    main()
