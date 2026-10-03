"""Descriptive: share of notified sites with zero Artportalen records within 250 m since 2016.

Run: .venv/bin/python scripts/near_site_share.py 21 17
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw import tools
from fw.config import OUT


def main() -> None:
    for lannr in sys.argv[1:]:
        ev = json.loads((OUT / f"eval_{lannr}.json").read_text())
        ids = ev["population_ids"]
        any_rec = qual = inside = 0
        for b in ids:
            r = tools._profile(b)["rings"]
            any_rec += r[250]["records"] == 0   # no record placed within 250 m, any uncertainty
            qual += r[250]["kept"] == 0          # none that could support a 250 m claim (uncertainty <= 250 m)
            inside += r[0]["records"] == 0
        n = len(ids)
        print(f"county {lannr}, window {ev['window'][0]}..{ev['window'][1]}, N={n}: "
              f"no records within 250 m {any_rec} ({100 * any_rec / n:.0f}%); "
              f"no qualifying records within 250 m {qual} ({100 * qual / n:.0f}%); "
              f"no records inside the polygon {inside} ({100 * inside / n:.0f}%)")


if __name__ == "__main__":
    main()
