"""
Procedural memory. Deliberately NOT a database, NOT a class hierarchy —
just a YAML file per incident type, loaded into a plain dict. The
routing logic in graph.py IS the procedure; this module just supplies
which steps to run and what "success" means for a given incident type.
"""
from pathlib import Path

import yaml

from Cortex import config

log = config.get_logger("cortex.procedure")


def load_procedure(incident_type: str) -> dict:
    """
    Load procedures/{incident_type}.yaml. Falls back to default.yaml
    if no matching file exists — Cortex should degrade gracefully to
    a generic investigation rather than stall on an unclassified incident.
    """
    procedures_dir = Path(config.PROCEDURES_DIR)
    target = procedures_dir / f"{incident_type}.yaml"

    if not target.exists():
        log.warning(
            f"No procedure file for incident_type='{incident_type}', "
            f"falling back to default.yaml"
        )
        target = procedures_dir / "default.yaml"

    with open(target) as f:
        procedure = yaml.safe_load(f)

    log.debug(f"Loaded procedure '{procedure['name']}' ({len(procedure['procedure'])} steps)")
    return procedure


def step_names(procedure: dict) -> list[str]:
    """Just the ordered list of step keys, for logging/debugging."""
    return [s["step"] for s in procedure["procedure"]]
