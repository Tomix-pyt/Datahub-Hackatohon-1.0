# Cortex

> "An agent that remembers not just the solution, but the shortest path to finding it."

Data teams re-investigate the same incidents over and over because the
fix lives in a Slack thread, not in the system that actually knows the
data. Cortex is a LangGraph agent that investigates data incidents by
walking DataHub's lineage graph, remembers what it finds as an
"Experience," and — critically — checks whether anything has actually
changed before deciding whether to trust that memory or investigate
fresh.

## Quick start (zero API keys needed)

```bash
pip install -e .
python app.py
```

This runs three incidents in **mock mode** (no live DataHub, no live
LLM) and demonstrates the three real paths:

1. **Cold start** — brand new incident, full investigation
2. **Clean reuse** — same failure pattern on a *different* asset →
   instant reuse (this is the fast path judges should see)
3. **Contradiction** — the *same* asset breaks again with nothing
   changed → Cortex refuses to blindly reuse the old fix and flags it
   for human review instead

Run the tests to confirm all three stay working as you build:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Project structure

```
cortex/
  config.py            settings + mock mode switch
  models.py             Incident, Experience, AssetSnapshot, DiffResult
  procedure.py           loads procedures/*.yaml (procedural memory — just config, not a database)
  memory_episodic.py     ChromaDB wrapper — the raw experience log, always written
  memory_semantic.py     DataHub client — promoted/generalized lessons only, and mock fixtures
  diff.py                 pure function: compares two AssetSnapshots
  reflection.py           the promotion gate + recurrence-contradiction check
  llm.py                  one function, swap the mock branch for a real call
  graph.py                 the LangGraph state machine — read this file to understand the whole system

procedures/
  schema_drift.yaml       the runbook for this incident type
  default.yaml             fallback for unclassified incidents

tests/test_graph.py       locks in the three-path behavior — run this after any change
```

## Switching off mock mode

1. Sign up for the [DataHub Cloud free trial](https://datahub.com/free-trial/)
2. Copy `.env.example` to `.env`, fill in `DATAHUB_GMS_URL`, `DATAHUB_TOKEN`, `ANTHROPIC_API_KEY`
3. Set `CORTEX_MOCK_MODE=false`
4. Implement the two `NotImplementedError` branches in `memory_semantic.py` and `llm.py` — they're clearly marked with `# TODO` comments for what's needed

## Debugging

Every node logs what it decided and why (`CORTEX_DEBUG=true` in `.env`,
which is the default). If something's behaving unexpectedly, read the
log line prefixed with the node name (`[RETRIEVE]`, `[VERIFY_DIFF]`,
`[REFLECT]`, etc.) before touching code — the whole point of this
structure is that the decision trail is visible, not hidden inside a
prompt.
