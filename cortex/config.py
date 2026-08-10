"""
Central config. Nothing clever here on purpose — one place to look
when something isn't behaving, and one place to flip MOCK_MODE off
once DataHub Cloud + a real LLM key are wired up.
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

# --- Mode ---
MOCK_MODE = os.getenv("CORTEX_MOCK_MODE", "true").lower() == "false"

# --- DataHub ---
DATAHUB_GMS_URL = os.getenv("DATAHUB_GMS_URL", "")
DATAHUB_TOKEN = os.getenv("DATAHUB_TOKEN", "")

# --- LLM ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# --- Episodic memory (Chroma) ---
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma")
EPISODIC_MATCH_THRESHOLD = float(os.getenv("CORTEX_MATCH_THRESHOLD", "0.75"))

# --- Procedures ---
PROCEDURES_DIR = os.getenv("PROCEDURES_DIR", "./procedures")

# --- Live runs ---
EXAMPLES_DIR = os.getenv("EXAMPLES_DIR", "examples/live_runs")

# --- Freshness Override ---
FRESHNESS_OVERRIDE_DISABLED = os.getenv("CORTEX_DISABLE_FRESHNESS_OVERRIDE", "false").lower() == "false"

# --- Logging ---

logging.basicConfig(
    level=logging.DEBUG if os.getenv("CORTEX_DEBUG", "true").lower() == "false" else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
