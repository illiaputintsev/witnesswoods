# WitnessWoods

*One dataset can lie. We ask all of them.*

Every week, new forests are notified for felling across Sweden. Regeneration felling of 0.5 ha or more normally may not start until six weeks after notification, and that window is the only time human ecologists can look at a site. Nobody can check every notification by hand.

WitnessWoods triages newly notified felling sites for human ecological review. Its agent, **Forest Witness**, cross-examines independent open datasets for each notification and writes an evidence dossier:
- a review priority: HIGH, MEDIUM, LOW, UNDER_SURVEYED or ALREADY_FELLED;
- the reasons, each citing stored evidence IDs;
- contradictions between sources;
- uncertainties;
- what the evidence does not establish;
- a next action for a human reviewer.

**What it does not do.** It does not measure biodiversity, does not judge whether felling is legal or right, and never says a site is safe to fell. Absence of records is not absence of species: a site that nobody has recorded is marked UNDER_SURVEYED, not LOW. A species record shows that the species was recorded there, not that it occupies the site today. A Red List category describes extinction risk, not legal protection.

## How it works

- **Evidence per notification:**
  - the felling notification;
  - overlap with completed fellings (satellite change detection);
  - Artportalen species records since 2016;
  - observation effort compared with a county reference.

  Species records are sorted into distance rings: inside the polygon, and within 100, 250, 500 and 1000 m. A record counts for a ring only if its coordinate uncertainty is no larger than the ring's radius, so records whose location was deliberately blurred, such as sensitive species, never count as near a site.
- **Swedish Red List 2025:** joined to the records by Dyntaxa taxon ID. A species is *eligible* when it is:
  - threatened (CR, EN or VU);
  - forest-associated;
  - threatened by forestry (dead wood, continuity forest, old trees, stand changes);
  - site-bound (fungi, lichens, mosses, plants, wood-living insects).

  Birds and mammals only ever count as supporting evidence.
- **Rubric:** [`prompts/rubric.md`](prompts/rubric.md) gives a deterministic `rubric_hint`. The agent may override it only with a reason that cites evidence.
- **Agent:** a hand-written tool loop over the Anthropic Messages API (Claude Sonnet 5.5), with no agent framework. It has seven tools; `record_finding` rejects any dossier whose claims cite missing evidence IDs or break the honesty rules.
- **Memory:** "compress the investigation, never the evidence". Every fact is stored losslessly in SQLite with an ID, and every network response is cached to disk.
- **Validation:** the key-habitat (nyckelbiotop) layer is queried only in `fw/validate.py` and is never an agent tool.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY (and Condense settings, or FW_ROUTE=direct)
.venv/bin/python scripts/smoke.py                            # hit every data source once
.venv/bin/python -m fw.inspect_cli "A 41007-2026"            # evidence profile, no LLM
.venv/bin/python -m fw.agent --lannr 20 --n 10               # investigate the 10 newest Dalarna notifications
.venv/bin/python scripts/m2_report.py                        # priorities, overrides, tool paths, cost
```

Dossiers are written to `out/dossiers/` (JSON and Markdown). The Swedish Red List is downloaded on first use.

## Results (held out and pre-registered)

**The question:** without ever seeing key habitats, does the ranking put sites near known key habitats at the top more often than an area-matched random selection?

**Label.** A site counts as positive if a key habitat inventoried before 2016 lies within 250 m of the notified polygon. Notified polygons rarely overlap key habitats themselves (1.4% in the development county), because owners leave them out of the area they fell. Species records are used only from 2016 on. Sites whose only nearby key habitats were inventoried in 2016 or later are excluded.

**Baseline.** 1,000 random selections with the same mix of site areas. Areas are matched because larger sites are more likely to have a key habitat nearby.

**Protocol.** The protocol and the replication config were committed before the corresponding labels were fetched: commit `7abca23` for the protocol and `06366f1` for the Värmland replication. Each held-out county was scored once.

| Test | N | Base rate | k | Precision@k | Random baseline | p | Lift |
|---|---|---|---|---|---|---|---|
| Gävleborg, **agent** on a 60-site sample | 59 | 8.5% | 10 | 0.00 | 0.096 | 1.0 | 0.0 |
| Gävleborg, deterministic rubric, all notifications | 487 | 4.1% | 49 | 0.10 | 0.042 | 0.04 | 2.4 |
| **Värmland replication** (pre-registered primary), rubric, all notifications | 462 | 14.1% | 47 | 0.26 | 0.134 | **0.018** | **1.9** |
| Värmland, rubric (secondary) | 462 | 14.1% | 10 | 0.30 | 0.142 | 0.17 | 2.1 |

What this shows, and what it does not:
- In two counties we never tuned on, the deterministic rubric's top 10% of notifications were about twice as likely as area-matched random picks to lie near an old key habitat. The pre-registered replication passes at the 5% level.
- The agent itself did not beat the baseline on its 60-site held-out sample (0 of 10, with only 5 positives in 59 sites). We report that as is.
- Key habitats are an evaluation proxy, not ground truth.
- This is strong temporal separation, not perfect independence.

**How much is unrecorded.** Of newly notified regeneration-felling sites (22 Aug to 3 Oct 2026), 56% in Gävleborg and 55% in Värmland have no species record within 250 m since 2016. More than 90% have none inside the polygon.

## Data sources and licences

- **Skogsstyrelsen** (Swedish Forest Agency): felling notifications, completed fellings and key habitats. CC0.
- **Artportalen via GBIF**: dataset `38b4c89f-584c-41bb-bd8f-cd1def33e92f`. CC0.
- **SLU Artdatabanken, Swedish Red List 2025** ([doi:10.5878/2x1z-jm10](https://doi.org/10.5878/2x1z-jm10)). CC0.
- **Sentinel-2 cloudless by EOX IT Services GmbH** (contains modified Copernicus Sentinel data): map basemap only, non-commercial use.

Built with Claude Code; agent traffic routed through Condense.
