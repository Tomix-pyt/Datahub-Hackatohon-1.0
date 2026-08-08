# Cortex

> "An agent that remembers not just the solution, but the shortest path to finding it."

Cortex is a LangGraph data-reliability agent. It receives an incident, checks episodic memory, verifies whether the current DataHub state still matches a previously successful investigation, traverses lineage when needed, proposes a fix for human approval, stores the experience, and promotes validated lessons back into DataHub.

## The important memory behavior

1. **Cold start:** no trusted precedent -> investigate the lineage graph.
2. **Same asset, changed state:** use the previous investigation as a seed, then investigate again.
3. **Same asset, unchanged state after a successful fix:** treat it as a contradiction and investigate again. The previous traversal/evidence is carried forward so Cortex can ask what it missed last time.
4. **Different asset, high-confidence matching pattern:** reuse the learned fix pattern without pretending that the two assets have identical snapshots.
5. **Rejected/failed fixes are never eligible for reuse.**

## Quick start

```bash
source .venv/bin/activate
python app.py
pytest tests/ -v
```

Mock mode is the default and requires no API keys.

### Mock schema-drift demo

The mock warehouse has two versions:

```bash
CORTEX_MOCK_VERSION=v1 python app.py
```

To simulate an upstream change for a later run:

```bash
CORTEX_MOCK_VERSION=v2 python app.py
```

## Real DataHub

Set:

```env
CORTEX_MOCK_MODE=false
DATAHUB_GMS_URL=http://localhost:8080
DATAHUB_TOKEN=
GROQ_API_KEY=...
GROQ_MODEL=...
```

For the local DataHub quickstart, GMS is normally port 8080. The SDK integration explicitly reads the current Dataset entity and uses DataHub's lineage client for upstream/downstream relationships.

## Important limitation

`Dataset.last_modified` is treated as a freshness **proxy** in the current MVP. It is not claimed to be a real pipeline execution timestamp. A production implementation should use an actual ingestion/run-status aspect or assertion when available.

## Structure

```text
cortex/
  graph.py              LangGraph control flow
  models.py             typed incident/snapshot/experience contracts
  memory_episodic.py    Chroma incident memory
  memory_semantic.py    DataHub state + promoted lessons
  diff.py               pure snapshot/traversal comparison
  reflection.py         recurrence guard + promotion gate
  llm.py                mock/live diagnosis and fix generation
  procedure.py          YAML runbooks

procedures/
  schema_drift.yaml
  freshness.yaml
  default.yaml

tests/test_graph.py     core warm/cold/recurrent behavior
```
