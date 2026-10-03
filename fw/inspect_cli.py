"""Evidence profile without any LLM.

  python -m fw.inspect_cli "A 41029-2026" ["A 40996-2026" ...]   full profiles
  python -m fw.inspect_cli --table 20 [--lannr 20]               summary table of the newest notifications
"""
import argparse
from collections import Counter

from fw import profile, skogs
from fw.profile import RINGS, _flags, ring_cell


def print_profile(p: dict) -> None:
    n, f, e = p["notification"], p["felling"], p["effort"]
    print("=" * 100)
    print(f"{n['beteckn']}  [{n['evidence_id']}]  {n['kommun']}, {n['lan']}")
    print(f"  {n['avverktyp']} | {n['skogstyp']} | received {n['inkomdatum']} (year ok: {n['date_year_ok']}) | "
          f"{n['anmald_ha']} ha notified, polygon {n['polygon_ha']} ha | status: {n['status']}")
    print(f"  completed felling overlap: {f['overlap_ha']} ha = {f['overlap_pct']}%  [{f['evidence_id']}]  "
          + ", ".join(f"{x['beteckn']} felled {x['avvdatum']}" for x in f["fellings"]))
    print(f"  effort within {e['radius_m']} m of the polygon since 2016: {e['records']} records, {e['species']} species, {e['days']} days; "
          f"county median {e['county_median']}, p25 {e['county_p25']} -> {e['level'].upper()} "
          f"(percentile {e['percentile']})  [{e['evidence_id']}]")
    j = p["join"]
    print(f"  GBIF records within 1 km: {j.get('records', 0)} (fetched {p['fetched']}, truncated: {p['truncated']}); "
          f"with Dyntaxa ID {j.get('with_dyntaxa_id', 0)}; red-listed by ID {j.get('redlisted_by_id', 0)}, "
          f"by name {j.get('redlisted_by_name', 0)}; no stated uncertainty {j.get('no_uncertainty', 0)}")
    print(f"  {'ring':>6} {'allow':>6} {'recs':>5} {'kept':>5} {'drop_u':>6} {'no_u':>5} {'spp':>4} {'days':>4}  "
          f"T/F/X/S  NT-S  mobile")
    for r in RINGS:
        x = p["rings"][r]
        print(f"  {r:>5}m {x['allowance_m']:>5}m {x['records']:>5} {x['kept']:>5} {x['dropped_uncertainty']:>6} "
              f"{x['dropped_no_uncertainty']:>5} {x['species']:>4} {x['days']:>4}  {ring_cell(x):<7}  "
              f"{x['nt_eligible']:>4}  {x['mobile_supporting']:>6}")
    widest = p["rings"][max(RINGS)]
    if widest["redlisted"]:
        print("  red-listed species within 1 km (flags: F forest, X felling-relevant, S site-bound / M mobile / o other):")
        order = {"CR": 0, "EN": 1, "VU": 2, "NT": 3, "DD": 4, "RE": 5}
        for sp in sorted(widest["redlisted"].values(), key=lambda s: (order.get(s["category"], 9), s["min_distance_m"])):
            first = min(r for r in RINGS if sp["taxon_id"] in p["rings"][r]["redlisted"])
            print(f"    {sp['category']:<2} {_flags(sp)} {sp['scientific_name']} ({sp['swedish_name']}, {sp['group']}): "
                  f"{sp['records']} rec, latest {sp['latest_year']}, nearest {sp['nearest_record']['dist_m']} m "
                  f"(±{sp['nearest_record']['unc_m']:.0f} m, {sp['nearest_record']['year']}), from ring {first} m  [{sp['evidence_id']}]")
    if widest["excluded_dd_re"]:
        print(f"  DD/RE (excluded from priority): {', '.join(widest['excluded_dd_re'])}")


def print_table(profiles: list[dict]) -> None:
    print("Cells per ring: T/F/X/S = threatened (CR/EN/VU) / +forest / +felling-relevant / +site-bound species")
    head = f"{'beteckn':<13} {'kommun':<12} {'ha':>5} {'felled%':>7} {'eff1km':>6} {'level':<12}"
    head += "".join(f" {str(r) + 'm':>8}" for r in RINGS) + f" {'NT-S@1k':>7} {'drop_u@100':>10}"
    print(head)
    for p in profiles:
        n, rings = p["notification"], p["rings"]
        line = (f"{n['beteckn']:<13} {n['kommun'][:12]:<12} {n['polygon_ha']:>5} {p['felling']['overlap_pct']:>7} "
                f"{p['effort']['records']:>6} {p['effort']['level']:<12}")
        line += "".join(f" {ring_cell(rings[r]):>8}" for r in RINGS)
        line += f" {rings[1000]['nt_eligible']:>7} {rings[100]['dropped_uncertainty']:>4}/{rings[100]['records']:<5}"
        print(line)
    tot = Counter()
    names = set()
    for p in profiles:
        for k, v in p["join"].items():
            if k == "name_fallbacks":
                names.update(v)
            else:
                tot[k] += v
    recs = tot["records"] or 1
    print(f"\nJoin over {tot['records']} records within 1 km: Dyntaxa ID present on {tot['with_dyntaxa_id']} "
          f"({100 * tot['with_dyntaxa_id'] / recs:.1f}%); red-listed via ID {tot['redlisted_by_id']}, "
          f"via name fallback {tot['redlisted_by_name']} {sorted(names) if names else ''}")
    levels = Counter(p["effort"]["level"] for p in profiles)
    print(f"Records without coordinate uncertainty: {tot['no_uncertainty']} of {tot['records']} "
          f"({100 * tot['no_uncertainty'] / recs:.1f}%)")
    print(f"Effort levels: {dict(levels)}; truncated fetches: {sum(p['truncated'] for p in profiles)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("beteckn", nargs="*")
    ap.add_argument("--table", type=int, default=0, help="summary table for the N newest notifications")
    ap.add_argument("--lannr", default="20")
    a = ap.parse_args()
    for b in a.beteckn:
        print_profile(profile.build(b))
    if a.table:
        notes = skogs.latest_notifications(a.lannr, a.table)
        print_table([profile.build(n["beteckn"]) for n in notes])


if __name__ == "__main__":
    main()
