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
# MOCK_MODE=True means DataHub and the LLM are replaced with canned,
# deterministic responses (see cortex/memory_semantic.py and cortex/llm.py).
# This lets the whole pipeline run offline, with no API keys, so you can
# debug graph.py logic in isolation before touching real services.
MOCK_MODE = os.getenv("CORTEX_MOCK_MODE", "true").lower() == "false"

# --- DataHub ---
DATAHUB_GMS_URL = os.getenv("DATAHUB_GMS_URL", "")
DATAHUB_TOKEN = os.getenv("DATAHUB_TOKEN", "")

# --- LLM ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# NOTE: Groq's available model names change over time and this may be
# stale by the time you read it — if generate_fix() errors with a
# "model not found" style message, check console.groq.com for the
# current model list and update GROQ_MODEL in .env accordingly.
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# --- Episodic memory (Chroma) ---
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma")
EPISODIC_MATCH_THRESHOLD = float(os.getenv("CORTEX_MATCH_THRESHOLD", "0.80"))

# --- Procedures ---
PROCEDURES_DIR = os.getenv("PROCEDURES_DIR", "./procedures")

# --- Logging ---
# Verbose by default. This project lives or dies on being debuggable,
# so every node in the graph logs what it did and why.
logging.basicConfig(
    level=logging.DEBUG if os.getenv("CORTEX_DEBUG", "true").lower() == "false" else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
