# Cortex architecture review and reconciliation

## What was intentionally good and should stay

### 1. Episodic memory is not the same thing as semantic memory
Chroma stores incident-level Experiences; DataHub receives only validated/generalized lessons. This is the right separation for the Cortex story.

### 2. Warm-path reuse is conditional, not blind
The strongest design decision is the rule: a prior success is evidence, not permission. Same-asset incidents must be checked against the current state before reuse.

### 3. Recurrence is a contradiction, not a duplicate
If the same asset produces the same symptom after a previously successful fix and the structural state is unchanged, Cortex investigates again. The prior traversal is passed into the new investigation as context.

### 4. Cross-asset reuse is pattern reuse, not snapshot equality
A precedent from another asset should not be diffed literally against the new asset. The previous implementation did that. The reconciled implementation uses semantic similarity for the pattern and reserves snapshot diffing for the same asset.

### 5. Lineage traversal is part of the memory artifact
An Experience now stores the traversal footprint and evidence used during the investigation. This makes "what did Cortex look at last time?" answerable instead of merely claiming that it remembers.

## Architectural problems found

### Shared-contract migration was incomplete
`retrieve()` had moved from dictionary precedents to typed `Experience` objects, but downstream nodes still expected dictionaries and the old `precedent` key. That was one migration applied to only part of the graph.

### Reflection had two incompatible contracts
The graph defined a `reflect(state)` function while the reflection module had the intended `reflect(experience)` contract. `reflect_node()` called the wrong function shape.

### Mock mode and debug mode parsed booleans backwards
The previous expressions enabled mock/debug when the environment variable said `false`. That made the safety net unreliable.

### Snapshot semantics were mixed
`last_run_status` was populated with `dataset.last_modified`. That makes a metadata timestamp look like a pipeline execution status and can manufacture false diffs. The reconciled real path leaves run status unknown unless an actual run-status source is available.

### Target-only diffing was insufficient for recurrence safety
A trigger asset can remain unchanged while an upstream producer changes. The reconciled warm-path check compares the stored traversal footprint against the current traversal footprint before deciding that "nothing changed."

### Downstream lineage was assumed to be a Dataset attribute
The DataHub SDK exposes lineage through the LineageClient. The real implementation now uses `client.lineage.get_lineage(..., direction="upstream"/"downstream")` rather than assuming `dataset.downstreams` exists.

### Promotion was not connected to the actual DataHub writer
The graph called a nonexistent `add_incident_tag_or_documentation()` method. Promotion now uses the existing `write_lesson()` boundary and gives the lesson a deterministic ID so repeated validated observations update the same lesson instead of creating random duplicates.

### Retrieval mixed identity and pattern
Putting the literal asset URN inside the embedding makes cross-asset semantic reuse harder. Identity is now a metadata filter; the embedding represents the failure pattern.

## Pragmatic MVP boundary

Cortex still does **not** automatically execute fixes. Human approval is the execution boundary. Also, the current real freshness implementation uses DataHub's `last_modified` as a clearly documented freshness proxy; it should be replaced by a true ingestion/run timestamp when that metadata is available in the target DataHub deployment.

This is intentional for the hackathon: the system is honest about what it can prove while keeping the core memory/reasoning loop demonstrable.
