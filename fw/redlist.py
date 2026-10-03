"""Swedish Red List 2025 (SLU Artdatabanken, CC0): load, normalise, flag, and match GBIF records.

Definitions here are the ones written up in DEFENCE.md; change both together.
"""
import re
import zipfile
from functools import lru_cache

import pandas as pd

from fw import config
from fw.cache import download_file

THREATENED = ("CR", "EN", "VU")
EXCLUDED = ("DD", "RE")  # listed separately, never used in priority logic

# Landscape type and forest biotope columns that mark a species as forest-associated
FOREST_LANDSCAPE = "Skog"  # values: Stor betydelse / Har betydelse / Ingen betydelse
FOREST_LANDSCAPE_YES = ("Stor betydelse", "Har betydelse")
FOREST_BIOTOPES = ("Barrskog", "Löv_barrblandskog", "Lövskog_övergripande", "Triviallövskog", "Ädellövskog")
FOREST_BIOTOPE_YES = ("Viktig", "Utnyttjas")

# Negative impact factors (main CSV columns) tied to forestry and felling.
# Paverkan_taxonrelationer.csv only holds the two related-taxon factors, so these
# columns in Rodlistade_arter_2025.csv are the authoritative source.
FELLING_FACTORS = (
    "Brist_på_död_ved",                  # lack of dead wood
    "Brist_på_kontinuitetsskog",         # lack of continuity forest (never clear-felled)
    "Brist_på_grova_eller_gamla_träd",   # lack of large or old trees
    "Markstörning_vid_skogsbruk",        # soil disturbance from forestry
    "Förändrad_beståndsstruktur",        # changed stand structure
    "Ökad_öppenhet_i_trädbärande_mark",  # increased openness of tree-covered land
    "Förändrad_trädslagssammansättning", # changed tree species composition
)
NEGATIVE = ("Stor negativ effekt", "Viss negativ effekt")

# Mobility, from Organismgrupp1
SITE_BOUND_GROUPS = ("Storsvampar", "Lavar", "Mossor", "Kärlväxter")
INSECT_GROUPS = ("Skalbaggar", "Tvåvingar", "Steklar", "Fjärilar", "Halvvingar", "Sländor", "Hopprätvingar")
DEAD_WOOD_YES = ("Viktigt", "Utnyttjas")  # Dött_träd_Betydelse, marks wood-living insects
MOBILE_GROUPS = ("Fåglar", "Däggdjur")


def normalise_category(raw: str) -> str:
    """'VU°*' -> 'VU', 'CR (PRE)' -> 'CR' (possibly extinct, still critically endangered)."""
    c = re.sub(r"[°*]", "", str(raw)).strip()
    return "CR" if c.startswith("CR") else c


def normalise_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def _mobility(row) -> str:
    g = row["Organismgrupp1"]
    if g in SITE_BOUND_GROUPS:
        return "site_bound"
    if g in INSECT_GROUPS and row["Dött_träd_Betydelse"] in DEAD_WOOD_YES:
        return "site_bound"  # wood-living insect
    if g in MOBILE_GROUPS:
        return "mobile"
    return "other"


def ensure_downloaded() -> None:
    download_file(config.REDLIST_URL, config.REDLIST_ZIP)
    if not config.REDLIST_CSV.exists():
        zipfile.ZipFile(config.REDLIST_ZIP).extractall(config.REDLIST_DIR)


@lru_cache(maxsize=1)
def load() -> pd.DataFrame:
    ensure_downloaded()
    df = pd.read_csv(config.REDLIST_CSV, sep=";", encoding="utf-8-sig", dtype=str).fillna("no data")
    df["taxon_id"] = df["TaxonId"].astype(int)
    df["category"] = df["Kategori"].map(normalise_category)
    df["forest_associated"] = df[FOREST_LANDSCAPE].isin(FOREST_LANDSCAPE_YES) | \
        df[list(FOREST_BIOTOPES)].isin(FOREST_BIOTOPE_YES).any(axis=1)
    df["felling_factors"] = df[list(FELLING_FACTORS)].apply(
        lambda r: [f for f in FELLING_FACTORS if r[f] in NEGATIVE], axis=1)
    df["felling_relevant"] = df["felling_factors"].str.len() > 0
    df["mobility"] = df.apply(_mobility, axis=1)
    df["name_key"] = df["Vetenskapligt_namn"].map(normalise_name)
    return df


@lru_cache(maxsize=1)
def _indexes():
    df = load()
    return df.set_index("taxon_id", drop=False), df.drop_duplicates("name_key").set_index("name_key", drop=False)


def dyntaxa_id(taxon_id_field: str | None) -> int | None:
    """'urn:lsid:dyntaxa.se:Taxon:220785' -> 220785."""
    m = re.search(r"dyntaxa\.se:Taxon:(\d+)", str(taxon_id_field or ""))
    return int(m.group(1)) if m else None


def match(taxon_id_field: str | None, species_name: str | None):
    """Return (red-list row or None, how) for a GBIF record. Dyntaxa ID first, then name."""
    by_id, by_name = _indexes()
    tid = dyntaxa_id(taxon_id_field)
    if tid is not None and tid in by_id.index:
        return by_id.loc[tid], "id"
    key = normalise_name(species_name)
    if key and key in by_name.index:
        return by_name.loc[key], "name"
    return None, None


def species_summary(row) -> dict:
    """Compact facts about one red-listed species, for profiles and tools."""
    return {
        "taxon_id": int(row["taxon_id"]), "scientific_name": row["Vetenskapligt_namn"],
        "swedish_name": row["Svenskt_namn"], "group": row["Organismgrupp1"],
        "category": row["category"], "category_raw": row["Kategori"], "criteria": row["Kriterium"],
        "forest_associated": bool(row["forest_associated"]), "felling_relevant": bool(row["felling_relevant"]),
        "felling_factors": list(row["felling_factors"]), "mobility": row["mobility"],
        "dalarna": row["Dalarna"], "gavleborg": row["Gävleborg"],
    }


def lookup(scientific_name: str) -> dict | None:
    """Full Red List entry for one species (used by the redlist_lookup tool in M2)."""
    row, _ = match(None, scientific_name)
    if row is None:
        return None
    df = load()
    landscapes = ["Skog", "Jordbrukslandskap", "Urban_miljö", "Fjäll", "Våtmark", "Sötvatten",
                  "Havsstrand", "Marin_miljö", "Brackvatten"]
    impact_cols = df.columns[df.columns.get_loc("Klimatförändringar"):df.columns.get_loc("Blekinge")]
    return {
        **species_summary(row),
        "landscapes": {c: row[c] for c in landscapes if row[c] not in ("Ingen betydelse", "no data")},
        "negative_impacts": {c: row[c] for c in impact_cols if row[c] in NEGATIVE},
        "habitats": {c: row[c] for c in FOREST_BIOTOPES if row[c] in FOREST_BIOTOPE_YES},
        "dead_wood": row["Dött_träd_Betydelse"], "living_trees": row["Levande_träd_Betydelse"],
    }
