"""Episodic memory store using ChromaDB for experience vector indexing and retrieval."""

import json
import logging
from typing import Any, Dict, List, Optional

import chromadb
from cortex import config
from cortex.models import AssetSnapshot, Experience

log = logging.getLogger(__name__)


def build_experience_embedding_text(exp: Experience) -> str:
    """Generates a rich structural fingerprint for ChromaDB vector embedding.

    Combines the asset identifier, incident classification, schema signature,
    lineage mismatches, root cause, and resolved fix into a single semantic string.
    """
    evidence = exp.evidence_context or {}
    schema_sample = evidence.get("target_schema_sample", [])
    mismatches = evidence.get("schema_mismatches", [])
    freshness = evidence.get("freshness", {})

    return (
        f"Asset URN: {exp.trigger_asset_urn}\n"
        f"Incident Type: {exp.incident_type}\n"
        f"Schema Signature: {schema_sample}\n"
        f"Observed Diffs: {mismatches}\n"
        f"Staleness Age (Hours): {freshness.get('age_hours')}\n"
        f"Root Cause: {exp.root_cause}\n"
        f"Proposed Fix: {exp.fix_proposed}"
    )


class EpisodicMemory:
    """ChromaDB-backed vector memory for storing and retrieving historical Cortex experiences."""

    def __init__(
        self,
        persist_directory: str = config.CHROMA_PERSIST_DIR,
        collection_name: str = "cortex_experiences",
    ):
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        count = self.collection.count()
        log.info(
            f"EpisodicMemory ready ({count} experiences on disk in '{collection_name}')"
        )

    def save(self, experience: Experience) -> None:
        """Saves an experience to ChromaDB using its rich structural fingerprint."""
        # Generate and assign the rich embedding text
        experience.embedding_text = build_experience_embedding_text(experience)

        # ChromaDB metadatas require flat primitive values (str, int, float, bool)
        metadata: Dict[str, Any] = {
            "incident_id": experience.incident_id or "",
            "incident_type": experience.incident_type or "unclassified",
            "trigger_asset_urn": experience.trigger_asset_urn or "",
            "timestamp": experience.timestamp or "",
            "procedure_used": experience.procedure_used or "default",
            "root_cause": experience.root_cause or "",
            "fix_proposed": experience.fix_proposed or "",
            "fix_applied": bool(experience.fix_applied),
            "outcome": experience.outcome or "pending",
            "nodes_visited": int(experience.nodes_visited or 0),
            "novel": bool(experience.novel),
            "promoted": bool(experience.promoted),
            # JSON serialize complex structures for metadata compliance
            "evidence_context_json": json.dumps(experience.evidence_context or {}),
            "snapshot_json": json.dumps(
                experience.snapshot.to_dict() if experience.snapshot else {}
            ),
        }

        self.collection.add(
            ids=[experience.id],
            documents=[experience.embedding_text],
            metadatas=[metadata],
        )
        log.info(
            f"[EPISODIC MEMORY] Saved experience {experience.id} with rich structural fingerprint."
        )

    def search(
        self, query_fingerprint: str, threshold: float = 0.70) -> Optional[Experience]:
        """Queries ChromaDB using a composite incident fingerprint.

        Returns the top matching Experience if its cosine similarity score
        meets or exceeds the threshold; otherwise returns None.
        """
        if self.collection.count() == 0:
            log.info("[EPISODIC MEMORY] Collection is empty — returning no precedent")
            return None

        results = self.collection.query(
            query_texts=[query_fingerprint],
            n_results=2,
        )
        experiences: List[Experience] = []

        if not results or not results.get("ids") or not results["ids"][0]:
            return experiences

        for i in range(len(results["ids"][0])):
            distance = results["distances"][0][i] if results.get("distances") else 1.0
            similarity_score = round(max(0.0, 1.0 - float(distance)), 4)

            # Filter out anything below threshold
            if similarity_score < threshold:
                continue

        exp_id = results["ids"][0][i]
        metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
        document = results["documents"][0][i] if results.get("documents") else ""

        # Reconstruct AssetSnapshot
        snapshot_data = json.loads(metadata.get("snapshot_json", "{}"))
        snapshot = AssetSnapshot(**snapshot_data) if snapshot_data else None

        # Reconstruct Experience
        exp = Experience(
            id=exp_id,
            incident_id=metadata.get("incident_id", ""),
            incident_type=metadata.get("incident_type", "unclassified"),
            trigger_asset_urn=metadata.get("trigger_asset_urn", ""),
            timestamp=metadata.get("timestamp", ""),
            procedure_used=metadata.get("procedure_used", "default"),
            snapshot=snapshot,
            root_cause=metadata.get("root_cause", ""),
            fix_proposed=metadata.get("fix_proposed", ""),
            fix_applied=metadata.get("fix_applied", False),
            outcome=metadata.get("outcome", "pending"),
            nodes_visited=metadata.get("nodes_visited", 0),
            similarity_score=similarity_score,
            novel=metadata.get("novel", True),
            promoted=metadata.get("promoted", False),
            evidence_context=json.loads(metadata.get("evidence_context_json", "{}")),
            embedding_text=document,
            )
        experiences.append(exp)
        return experiences