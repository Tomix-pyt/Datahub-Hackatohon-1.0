"""
Wraps ChromaDB. Stores raw Experience objects, unconditionally, on
every run (success or failure, novel or duplicate) — see graph.py's
`store` node. This is deliberately separate from memory_semantic.py:
episodic is the complete raw log, semantic (DataHub) only gets the
generalized, validated lessons that survive the Reflect gate.
"""
import dataclasses
import hashlib
import json
import re

import chromadb

from Cortex import config
from Cortex.models import Experience

log = config.get_logger("cortex.memory_episodic")

# A new EpisodicMemory() gets constructed inside almost every graph node
# (retrieve, store...). Opening a fresh chromadb.PersistentClient each
# time hits the same on-disk SQLite file repeatedly within one process
# and intermittently throws "attempt to write a readonly database" from
# lock contention. Caching one client per process fixes it cleanly.
_client = None


def _get_client():
    global _client
    if _client is None:
        if config.CHROMA_PERSIST_DIR == ":memory:":
            # In-memory client: no on-disk SQLite file, no lock contention.
            # Tests should use this — it's also just faster and avoids
            # leaving test artifacts on disk between runs.
            _client = chromadb.EphemeralClient()
        else:
            _client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
    return _client


def reset_client():
    """For tests: drop the cached client so the next EpisodicMemory()
    reopens against a freshly-cleaned directory instead of holding
    stale handles to deleted files."""
    global _client
    _client = None


def _fake_embedding(text: str, dim: int = 64) -> list[float]:
    """
    Deterministic, dependency-free 'embedding' for offline dev/testing,
    using the hashing trick (bag-of-words -> fixed-size vector by hashing
    each token into a bucket). Unlike a raw text hash, this DOES preserve
    similarity: two strings sharing words land close together, which is
    what retrieval actually needs to demo the warm-recall path.

    Swap for a real embedding model (sentence-transformers, or an
    Anthropic/OpenAI embeddings call) before the real demo — this is
    intentionally crude, just enough to prove the plumbing works.
    """
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    for token in tokens:
        bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % dim
        vec[bucket] += 1.0
    return vec


class EpisodicMemory:
    def __init__(self):
        self.client = _get_client()
        self.collection = self.client.get_or_create_collection(
            "cortex_experiences",
            metadata={"hnsw:space": "cosine"},  # so distance -> similarity is a clean 1 - distance
        )
        log.info(f"EpisodicMemory ready ({self.collection.count()} experiences on disk)")

    def add(self, experience: Experience) -> None:
        embedding = _fake_embedding(experience.embedding_text)
        # dataclasses.asdict recurses into nested dataclasses (like the
        # embedded AssetSnapshot) and turns them into plain dicts, unlike
        # experience.__dict__ which leaves nested dataclass objects intact
        # and unserializable — that mismatch was silently corrupting what
        # got stored and breaking the read-back on the next run.
        self.collection.add(
            ids=[experience.id],
            embeddings=[embedding],
            metadatas=[{"raw": json.dumps(dataclasses.asdict(experience), default=str)}],
            documents=[experience.embedding_text],
        )
        log.debug(f"Stored experience {experience.id}: '{experience.embedding_text}'")

    def query(self, text: str, top_k: int = 3) -> list[dict]:
        """
        Returns a list of {experience: dict, score: float}, best match
        first. score is a similarity in [0, 1] — higher is more similar.
        """
        if self.collection.count() == 0:
            log.debug("Episodic memory is empty — nothing to retrieve yet")
            return []

        embedding = _fake_embedding(text)
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(top_k, self.collection.count()),
        )

        matches = []
        for i in range(len(result["ids"][0])):
            distance = result["distances"][0][i]
            score = 1.0 - distance  # chroma default is L2/cosine distance; smaller = more similar
            raw = json.loads(result["metadatas"][0][i]["raw"])
            matches.append({"experience": raw, "score": score})

        log.debug(f"Retrieved {len(matches)} candidate(s) for query '{text}', top score={matches[0]['score']:.3f}")
        return matches
