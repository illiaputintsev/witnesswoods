# Priority rubric v1

This rubric orders sites for human ecological review. It does not measure biodiversity and it never makes a site "safe to fell".

## Terms

- **Threatened**: Swedish Red List 2025 category CR, EN or VU. NT is counted separately. DD and RE never affect priority.
- **Eligible species**: threatened, forest-associated, felling-relevant (a forestry or felling impact factor, or lack of dead wood, continuity forest or large/old trees, is among its threats) and site-bound (fungi, lichens, mosses, vascular plants, wood-living insects). Birds and mammals are mobile and only ever supporting evidence.
- **Site-bound forest NT species**: NT species that are forest-associated, felling-relevant and site-bound.
- **Within X m**: the record's reported position is within X m of the polygon edge, and its coordinate uncertainty is no larger than X (inside the polygon: no larger than the site's equivalent radius, at most 100 m). The species tool already applies this rule.
- **Effort**: Artportalen records since 2016 within 1000 m of the polygon with coordinate uncertainty of at most 1000 m (the records the 1000 m ring keeps), compared with the median of a fixed sample of 50 notifications in the same county.

## Priorities (first match wins, top to bottom)

1. **ALREADY_FELLED**: completed felling (satellite change detection) covers 50% or more of the notified polygon.
2. **HIGH**: at least 1 eligible species within 250 m.
3. **MEDIUM**: any of
   - eligible species recorded beyond 250 m but within 1000 m, none within 250 m;
   - at least 3 site-bound forest NT species within 250 m;
   - an unresolved contradiction, for example effort below the county median while threatened species are recorded within 1000 m.
4. **LOW**: effort at or above the county median, no eligible species within 1000 m, and fewer than 3 site-bound forest NT species within 250 m.
5. **UNDER_SURVEYED**: effort below the county median and none of the above.

Hard limits that no override can cross: LOW needs effort at or above the county median (a resolved contradiction at a below-median site falls through to UNDER_SURVEYED), and ALREADY_FELLED needs completed felling over at least 50% of the polygon.

## Rubric hint and overrides

The species and effort tools return a deterministic `rubric_hint`: this rubric applied mechanically to all rings and the effort figure. Treat it as the default. You may choose a different priority only when the evidence gives a concrete reason the mechanical rule misreads this site, for example:

- the contradiction that triggered MEDIUM is resolved (the threatened species near a low-effort site are mobile birds or open-land species, so they say nothing about the forest stand);
- the only eligible record is old, at the edge of its ring, or its uncertainty is large relative to the site;
- a partial completed felling changes what remains to be reviewed.

When you override, pass `override_reason` with a plain explanation and the evidence IDs it rests on. Never override towards a lower priority just to produce a cleaner answer.
