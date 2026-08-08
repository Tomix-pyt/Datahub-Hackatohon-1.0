"""Chroma-backed episodic memory.

The store is intentionally dumb: every completed incident becomes an
Experience. Retrieval decides whether that experience is trustworthy enough
to influence routing; it is not itself the decision-maker.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import chromadb

from cortex import config
from cortex.models import AssetSnapshot, Experience

log = logging.getLogger(__name__)

_client: Optional["EpisodicMemory"] = None


def build_experience_embedding_text(exp: Experience) -> str:
    evidence = exp.evidence_context or {}
    schema_sample = evidence.get("target_schema_sample", [])
    mismatches = evidence.get("schema_mismatches", [])
    freshness = evidence.get("freshness", {})
    return (
        f"Incident Type: {exp.incident_type}\n"
        f"Symptom: {evidence.get('incident_description', '')}\n"
        f"Schema Signature: {schema_sample}\n"
        f"Observed Diffs: {mismatches}\n"
        f"Staleness Age (Hours): {freshness.get('age_hours')}\n"
        f"Root Cause: {exp.root_cause}\n"
        f"Proposed Fix: {exp.fix_proposed}"
    )


class EpisodicMemory:
    def __init__(self, persist_directory: str | None = None, collection_name: str = "cortex_experiences"):
        path = persist_directory or config.CHROMA_PERSIST_DIR
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        log.info("EpisodicMemory ready (%d experiences)", self.collection.count())

    def save(self, experience: Experience) -> None:
        experience.embedding_text = build_experience_embedding_text(experience)
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
            "matched_prior_experience_id": experience.matched_prior_experience_id or "",
            "similarity_score": float(experience.similarity_score or 0.0),
            "evidence_context_json": json.dumps(experience.evidence_context or {}),
            "snapshot_json": json.dumps(experience.snapshot.to_dict() if experience.snapshot else {}),
        }
        # update() is deliberate: an Experience ID is immutable and should not
        # accidentally be duplicated if a caller retries a node.
        self.collection.upsert(
            ids=[experience.id],
            documents=[experience.embedding_text],
            metadatas=[metadata],
        )
        log.info("[EPISODIC MEMORY] Saved %s", experience.id)

    def search(
        self,
        query_fingerprint: str,
        threshold: float | None = None,
        asset_urn: str | None = None,
        n_results: int = 5,
    ) -> Optional[Experience]:
        """Return the best *successful* precedent above threshold.

        ``asset_urn`` is an optional exact-asset filter. Cross-asset retrieval
        deliberately omits it so the embedding represents the failure pattern,
        not the literal asset name.
        """
        threshold = config.EPISODIC_MATCH_THRESHOLD if threshold is None else threshold
        if self.collection.count() == 0:
            return None

        where: dict | None = None
        if asset_urn:
            where = {
                "$and": [
                    {"trigger_asset_urn": asset_urn},
                    {"fix_applied": True},
                    {"outcome": "success"},
                ]
            }
        else:
            where = {"$and": [{"fix_applied": True}, {"outcome": "success"}]}

        results = self.collection.query(
            query_texts=[query_fingerprint],
            n_results=min(n_results, self.collection.count()),
            where=where,
        )
        ids = results.get("ids", [[]])[0]
        if not ids:
            return None

        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        documents = results.get("documents", [[]])[0]

        # Chroma returns nearest-first, so choose the first result above the
        # threshold rather than accepting a weak top candidate blindly.
        for idx, exp_id in enumerate(ids):
            distance = float(distances[idx]) if idx < len(distances) else 1.0
            similarity = round(max(0.0, 1.0 - distance), 4)
            if similarity < threshold:
                continue

            metadata = metadatas[idx] if idx < len(metadatas) else {}
            document = documents[idx] if idx < len(documents) else ""
            snapshot_data = json.loads(metadata.get("snapshot_json", "{}"))
            snapshot = AssetSnapshot(**snapshot_data) if snapshot_data else None
            evidence = json.loads(metadata.get("evidence_context_json", "{}"))

            return Experience(
                id=exp_id,
                incident_id=metadata.get("incident_id", ""),
                incident_type=metadata.get("incident_type", "unclassified"),
                trigger_asset_urn=metadata.get("trigger_asset_urn", ""),
                timestamp=metadata.get("timestamp", ""),
                procedure_used=metadata.get("procedure_used", "default"),
                snapshot=snapshot,
                root_cause=metadata.get("root_cause", ""),
                fix_proposed=metadata.get("fix_proposed", ""),
                fix_applied=bool(metadata.get("fix_applied", False)),
                outcome=metadata.get("outcome", "pending"),
                nodes_visited=int(metadata.get("nodes_visited", 0)),
                matched_prior_experience_id=metadata.get("matched_prior_experience_id") or None,
                similarity_score=similarity,
                novel=bool(metadata.get("novel", True)),
                promoted=bool(metadata.get("promoted", False)),
                evidence_context=evidence,
                embedding_text=document,
            )

        return None

    def get_successful_for_pattern(self, asset_urn: str, root_cause: str) -> list[Experience]:
        """Small read helper used by promotion aggregation."""
        if self.collection.count() == 0:
            return []
        results = self.collection.get(
            where={
                "$and": [
                    {"trigger_asset_urn": asset_urn},
                    {"fix_applied": True},
                    {"outcome": "success"},
                ]
            },
            include=["metadatas", "documents"],
        )
        experiences: list[Experience] = []
        for exp_id, metadata, document in zip(
            results.get("ids", []), results.get("metadatas", []), results.get("documents", [])
        ):
            if metadata.get("root_cause") == root_cause:
                experiences.append(
                    Experience(
                        id=exp_id,
                        incident_id=metadata.get("incident_id", ""),
                        incident_type=metadata.get("incident_type", "unclassified"),
                        trigger_asset_urn=metadata.get("trigger_asset_urn", ""),
                        timestamp=metadata.get("timestamp", ""),
                        root_cause=metadata.get("root_cause", ""),
                        fix_proposed=metadata.get("fix_proposed", ""),
                        fix_applied=True,
                        outcome="success",
                        evidence_context=json.loads(metadata.get("evidence_context_json", "{}")),
                        embedding_text=document or "",
                    )
                )
        return experiences


def get_client() -> EpisodicMemory:
    global _client
    if _client is None:
        _client = EpisodicMemory()
    return _client


def reset_client() -> None:
    global _client
    _client = None
