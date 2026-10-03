"""Follow-up chat about one site: the agent gets the full dossier and evidence context, can search the web
for species ecology, and can return small charts built only from numbers in the context.
"""
import json
import os
import time

import anthropic

from fw import agent, config, tools

MAX_TOKENS = 6000
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
ARTFAKTA = "https://artfakta.se/taxa/{}"

SYSTEM = """You are Forest Witness, answering follow-up questions from a human reviewer about ONE notified felling site in Sweden. The full dossier and evidence for the site are in <site_context>.

How to answer:
- Be concrete and brief; use short paragraphs or bullets.
- Facts about the site must come from <site_context>; cite their evidence IDs like [E-0123].
- For species questions, use <site_context>.species_facts first: it is the official Swedish Red List 2025 entry for each red-listed species near the site (category, criteria, landscapes, forest habitats, negative impact factors, dependence on dead wood and living trees) plus its Artfakta page. Cite it as "Swedish Red List 2025" and link the Artfakta page.
- Use web_search only for what species_facts does not cover (for example identification, survey season, substrate details), at most one search per species, eligible species first. Name the source for anything you take from the web.
- Never talk about tool limits or apologise for them. If a fact is not available, say briefly what the reviewer can check (the species' Artfakta page).
- Tables are fine (markdown).
- When the reviewer asks for a plan, give a practical field-visit plan tied to this site's records, distances and the window-closing date.
- When a chart helps, add one fenced block exactly like:
```chart
{"type": "bar", "title": "...", "x": ["2017", "2018"], "series": [{"name": "records", "values": [3, 5]}], "unit": "records"}
```
  ("type" is "bar" or "line"). Build charts from <site_context>.aggregates whenever one fits (they are exact counts);
  otherwise chart only numbers that are in <site_context>. Never invent or re-count data, and keep the text consistent with the chart.

Hard rules (same as the dossier):
- Never say a site is safe to fell. You do not measure biodiversity and you do not judge whether felling is legal.
- Absence of records is not absence of species. A record shows a species was recorded, not that it occupies the site today.
- A Red List category describes extinction risk, not legal protection.
- Some sensitive species are hidden or coordinate-obscured in public data. Never try to infer where they are.
- If the context cannot answer a question, say so plainly."""


def site_context(beteckn: str) -> str:
    """Everything the reviewer sees, plus the full red-listed record table from the evidence profile."""
    p = tools._profile(beteckn)
    f = config.ROOT / "web" / "data" / "sites" / f"{beteckn.replace(' ', '_')}.json"
    d = json.loads(f.read_text()) if f.exists() else {}
    keep = {k: d.get(k) for k in ("kind", "priority", "rubric_hint", "override_reason", "notification", "window_closes",
                                    "reasons", "contradictions", "uncertainties", "not_established", "next_action",
                                    "species_lines", "context", "rubric_status", "evidence")}
    for s in keep.get("species_lines") or []:
        s.pop("photo", None)
    records, facts = [], []
    from fw import redlist
    wide = sorted(p["rings"][1000]["redlisted"].values(),
                  key=lambda s: (not tools.rubric._eligible(s), {"CR": 0, "EN": 1, "VU": 2, "NT": 3}.get(s["category"], 9), s["min_distance_m"]))
    for sp in wide:
        for r in sp["recs"][:40]:
            records.append([sp["scientific_name"], sp["swedish_name"], sp["category"], sp["group"], sp["mobility"],
                            r["year"], r["dist_m"], r["unc_m"], sp.get("evidence_id")])
        if len(facts) < 15 and sp["category"] in ("CR", "EN", "VU", "NT"):
            e = redlist.lookup(sp["scientific_name"]) or {}
            facts.append({"scientific": sp["scientific_name"], "swedish": sp["swedish_name"], "group": sp["group"],
                          "category": e.get("category"), "criteria": e.get("criteria"), "eligible": tools.rubric._eligible(sp),
                          "mobility": sp["mobility"], "landscapes": e.get("landscapes"), "forest_habitats": e.get("habitats"),
                          "negative_impacts": e.get("negative_impacts"), "dead_wood": e.get("dead_wood"),
                          "living_trees": e.get("living_trees"), "evidence_id": sp.get("evidence_id"),
                          "nearest_m": sp["min_distance_m"], "records_within_1000m": sp["records"],
                          "artfakta": ARTFAKTA.format(sp["taxon_id"])})
    e = p["effort"]
    # Exact, precomputed aggregates so charts never depend on the model counting rows
    from collections import Counter
    agg = {}
    for ring in (250, 1000):
        red = [s for s in p["rings"][ring]["redlisted"].values() if s["category"] in ("CR", "EN", "VU", "NT")]
        years = Counter(r["year"] for s in red for r in s["recs"])
        agg[f"redlisted_records_by_year_within_{ring}m"] = dict(sorted((str(k), v) for k, v in years.items() if k))
        agg[f"redlisted_species_by_category_within_{ring}m"] = dict(Counter(s["category"] for s in red))
        agg[f"redlisted_records_by_species_within_{ring}m"] = {s["scientific_name"]: s["records"] for s in red}
    ctx = {
        "aggregates": agg,
        "dossier": keep,
        "effort": {k: e.get(k) for k in ("records", "species", "days", "county_median", "level", "near_site", "reference")},
        "rings": {str(r): {k: p["rings"][r][k] for k in ("records", "kept", "species", "days", "threatened", "eligible", "nt_eligible")}
                  for r in p["rings"]},
        "felling_overlap": {k: p["felling"][k] for k in ("overlap_ha", "overlap_pct", "fellings")},
        "species_facts": facts,
        "redlisted_records_columns": ["scientific", "swedish", "category", "group", "mobility", "year", "dist_m", "unc_m", "evidence_id"],
        "redlisted_records": records[:400],
    }
    return json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))


def ask(beteckn: str, messages: list[dict]) -> dict:
    """messages: [{role: user|assistant, content: str}] ending with the reviewer's question."""
    if not (os.getenv("ANTHROPIC_API_KEY") and config.FW_MODEL):
        raise RuntimeError("chat needs ANTHROPIC_API_KEY and FW_MODEL")
    system = [{"type": "text", "text": SYSTEM}, {"type": "text", "text": f"<site_context>{site_context(beteckn)}</site_context>",
                                                 "cache_control": {"type": "ephemeral"}}]
    convo = [{"role": m["role"], "content": str(m["content"])[:4000]} for m in messages[-12:]]
    t0, route = time.time(), config.FW_ROUTE
    params = dict(model=config.FW_MODEL, max_tokens=MAX_TOKENS, system=system, tools=[WEB_SEARCH], messages=convo,
                  output_config={"effort": "medium"})
    try:
        client = agent.make_client(route, f"ww-chat-{beteckn.replace(' ', '_')}")
        resp = client.messages.create(**params)
    except anthropic.APIStatusError:
        route = "direct"  # e.g. a proxy that does not pass server tools through
        resp = agent.make_client("direct", "").messages.create(**params)
    for _ in range(4):  # server tools may pause a long turn
        if resp.stop_reason != "pause_turn":
            break
        params["messages"] = convo + [{"role": "assistant", "content": resp.content}]
        resp = agent.make_client(route, f"ww-chat-{beteckn.replace(' ', '_')}").messages.create(**params)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    sources = []
    for b in resp.content:
        if b.type == "web_search_tool_result" and isinstance(b.content, list):
            sources += [{"title": r.title, "url": r.url} for r in b.content if getattr(r, "url", None)]
        for c in getattr(b, "citations", None) or []:
            if getattr(c, "url", None):
                sources.append({"title": getattr(c, "title", c.url), "url": c.url})
    seen, uniq = set(), []
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"]); uniq.append(s)
    u = agent._usage(resp)
    usd = agent.cost_usd(config.FW_MODEL, u)
    with (config.OUT / "log.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "chat", "notification": beteckn,
                            "route": route, "usage": u, "usd": round(usd, 5), "latency_s": round(time.time() - t0, 1)}) + "\n")
    return {"text": text or "(no answer)", "sources": uniq[:8], "route": route, "usd": round(usd, 4)}
