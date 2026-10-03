"""Paths, URLs and settings. Everything configurable lives here or in .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
CACHE_DIR = DATA / "cache"
OUT = ROOT / "out"

# Swedish Red List 2025 (SLU Artdatabanken, CC0), researchdata.se dataset 2026-63
REDLIST_URL = "https://api.researchdata.se/dataset/2026-63/1/file/zip"
REDLIST_ZIP = DATA / "redlist_2025.zip"
REDLIST_DIR = DATA / "redlist_2025"
REDLIST_CSV = REDLIST_DIR / "2026-63-1" / "data" / "Rodlistade_arter_2025.csv"

# Skogsstyrelsen ArcGIS layers (CC0)
SKOGS_BASE = "https://geodpags.skogsstyrelsen.se/arcgis/rest/services/Geodataportal"
NOTIFICATIONS_URL = f"{SKOGS_BASE}/GeodataportalVisaSkogsbruk/MapServer/5"
COMPLETED_FELLINGS_URL = f"{SKOGS_BASE}/GeodataportalVisaSkogsbruk/MapServer/6"
KEY_HABITATS_URL = f"{SKOGS_BASE}/GeodataportalVisaNaturkultur/MapServer/3"  # evaluation only

# GBIF: Artportalen dataset
GBIF_API = "https://api.gbif.org/v1"
ARTPORTALEN_DATASET = "38b4c89f-584c-41bb-bd8f-cd1def33e92f"

USER_AGENT = os.getenv("GBIF_USER_AGENT", "witnesswoods-hackathon/0.1")

FW_ROUTE = os.getenv("FW_ROUTE", "direct")
FW_MODEL = os.getenv("FW_MODEL", "")
FW_BUDGET_USD = float(os.getenv("FW_BUDGET_USD", "5"))
CONDENSE_BASE_URL = os.getenv("CONDENSE_BASE_URL", "")

# USD per million tokens (Anthropic list prices, Oct 2026). Cache write = 5-minute TTL (1.25x input).
PRICES = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
}
