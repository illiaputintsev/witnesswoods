"""Milestone 2 acceptance report for one agent run, read from out/log.jsonl and out/dossiers/.

Run: .venv/bin/python scripts/m2_report.py [run_id]   (default: the latest run in the log)
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw import evidence, rank
from fw.config import OUT


def main() -> None:
    events = [json.loads(l) for l in (OUT / "log.jsonl").read_text().splitlines() if l.strip()]
    run_id = sys.argv[1] if len(sys.argv) > 1 else events[-1]["run_id"]
    ev = [e for e in events if e["run_id"] == run_id]
    findings = [e for e in ev if e["type"] == "finding"]
    ids = [f["notification"] for f in findings]
    dossiers = {d["beteckn"]: d for d in rank.load_dossiers(ids) if d.get("run_id") == run_id}

    print(f"run {run_id}: {len(findings)} notifications, route {ev[0]['route']}")
    print("\n1. Priority distribution")
    print("  ", dict(Counter(f["priority"] for f in findings)))

    print("\n2. Agent priority vs rubric_hint")
    for b in ids:
        d = dossiers[b]
        h = d["rubric_hint"]["priority"]
        mark = "same" if h == d["priority"] else "OVERRIDE"
        print(f"   {b}: agent {d['priority']:<15} hint {h:<15} {mark}")
        if d.get("override_reason"):
            print(f"      reason: {d['override_reason']['text']} {d['override_reason']['evidence_ids']}")

    print("\n3. Every reason cites existing evidence IDs")
    bad = []
    for b, d in dossiers.items():
        cited = {i for r in d["reasons"] + d["contradictions"] for i in r["evidence_ids"]}
        found = {e["id"]: e for e in evidence.get(sorted(cited))}
        bad += [(b, i) for i in cited if i not in found or found[i]["notification"] not in (b, "redlist")]
        bad += [(b, "reason without IDs") for r in d["reasons"] if not r["evidence_ids"]]
    print(f"   {sum(len(d['reasons']) for d in dossiers.values())} reasons checked, problems: {bad or 'none'}")

    print("\n4. Tool-call paths")
    paths = defaultdict(list)
    for e in ev:
        if e["type"] == "tool_call":
            a = {k: v for k, v in e["args"].items() if k not in ("beteckn",)}
            short = e["tool"] + (f"({a['buffer_m']})" if "buffer_m" in a else "") + ("!" if e["error"] else "")
            paths[e["notification"]].append(short)
    distinct = Counter(" > ".join(p) for p in paths.values())
    for b in ids:
        print(f"   {b} [{dossiers[b]['priority']}]: {' > '.join(paths[b])}")
    print(f"   distinct paths: {len(distinct)} of {len(ids)} ('!' = rejected call)")

    print("\n5. Tokens and cost")
    t = Counter()
    for e in ev:
        if e["type"] == "model_call":
            t.update(e["usage"])
            t["calls"] += 1
            t["usd_x1e5"] += round(e["usd"] * 1e5)
    print(f"   model calls {t['calls']}; input (uncached) {t['input']}, cache write {t['cache_write']}, "
          f"cache read {t['cache_read']}, output {t['output']}; cost ${t['usd_x1e5'] / 1e5:.3f}")

    print("\n6. Condense session ids")
    print("  ", sorted({e["session_id"] for e in ev if e.get("session_id")}))

    print("\n7. Ranked order")
    for i, d in enumerate(rank.ranked(ids), 1):
        print(f"   {i:>2}. {d['beteckn']} {d['priority']:<15} {d['counts']}")


if __name__ == "__main__":
    main()
