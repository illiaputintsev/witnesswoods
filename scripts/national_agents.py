"""Run the agent through Condense on the top sites per county by the deterministic rubric (national week).

Skips sites that already have an agent dossier. Three concurrent processes share the budget.
Run: .venv/bin/python scripts/national_agents.py [--per-county 5] [--budget 4]
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fw import config, tools  # noqa: E402
import national  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-county", type=int, default=5)
    ap.add_argument("--budget", type=float, default=4.0)
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    top = national.ranked_by_county(a.per_county)
    todo = [b for l in sorted(top) for b in top[l]
            if not (tools.DOSSIERS / f"{b.replace(' ', '_')}.json").exists()]
    print(f"top {a.per_county} per county: {sum(len(v) for v in top.values())} sites; {len(todo)} need an agent run")
    (config.OUT / "national_top.json").write_text(json.dumps(top, indent=1))
    groups = [todo[i::a.procs] for i in range(a.procs)]
    env = {**os.environ, "FW_BUDGET_USD": f"{a.budget / a.procs:.2f}"}
    procs = []
    for i, g in enumerate(groups):
        if not g:
            continue
        args = [sys.executable, "-u", "-m", "fw.agent", *[b.replace(" ", "_") for b in g], "--route", "condense",
                "--label", f"national{i + 1}"]
        log = open(config.OUT / f"national_agents_{i + 1}.txt", "w")
        procs.append(subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
    for p in procs:
        p.wait()
    print("done")


if __name__ == "__main__":
    main()
