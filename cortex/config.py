"""Central configuration for Cortex.

Keep environment switches here so the rest of the codebase does not have to
interpret strings such as ``"true"``/``"false"`` repeatedly.  The defaults
are deliberately safe for local development: mock DataHub + mock LLM.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


# --- Mode -----------------------------------------------------------------
MOCK_MODE = _env_bool("CORTEX_MOCK_MODE", True)
MOCK_CURRENT_VERSION = os.getenv("CORTEX_MOCK_VERSION", "v1")

# --- DataHub --------------------------------------------------------------
DATAHUB_GMS_URL = os.getenv("DATAHUB_GMS_URL", "http://localhost:8080")
# Support both names because DataHub itself commonly uses DATAHUB_GMS_TOKEN.
DATAHUB_TOKEN = os.getenv("DATAHUB_TOKEN") or os.getenv("DATAHUB_GMS_TOKEN", "")

# --- LLM ------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# --- Episodic memory ------------------------------------------------------
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./.chroma_db")
EPISODIC_MATCH_THRESHOLD = float(os.getenv("CORTEX_MATCH_THRESHOLD", "0.80"))
CROSS_ASSET_REUSE_THRESHOLD = float(os.getenv("CORTEX_CROSS_ASSET_THRESHOLD", "0.88"))

# --- Investigation --------------------------------------------------------
LINEAGE_MAX_DEPTH = int(os.getenv("CORTEX_LINEAGE_MAX_DEPTH", "2"))
FRESHNESS_SLA_HOURS = float(os.getenv("CORTEX_FRESHNESS_SLA_HOURS", "24"))

# --- Procedures -----------------------------------------------------------
PROCEDURES_DIR = os.getenv("PROCEDURES_DIR", "./procedures")

# --- Logging --------------------------------------------------------------
_DEBUG = _env_bool("CORTEX_DEBUG", True)
logging.basicConfig(
    level=logging.DEBUG if _DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
