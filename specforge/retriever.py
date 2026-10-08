import logging
import hashlib
import re
from collections import defaultdict

import numpy as np
from rank_bm25 import BM25Okapi

from .config import Settings
from .ingest import Chunk

log = logging.getLogger(__name__)
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60
_STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were will with "
            "shall should must can may".split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP]


class HybridRetriever:
    """BM25 keyword search fused with dense embeddings via Reciprocal Rank Fusion."""

    def __init__(self, chunks: list[Chunk], settings: Settings):
        self.chunks = chunks
        self.by_id = {c.id: c for c in chunks}
        self.bm25 = BM25Okapi([_tokens(c.text) for c in chunks])
        self.embedder = None
        self.vectors = None
        if settings.use_dense:
            self._build_dense(settings)

    def _build_dense(self, settings: Settings) -> None:
        from fastembed import TextEmbedding

        self.embedder = TextEmbedding(EMBED_MODEL, cache_dir=str(settings.cache_dir / "fastembed"))
        digest = hashlib.sha256("\x00".join(c.text for c in self.chunks).encode()).hexdigest()[:16]
        path = settings.cache_dir / "embeddings" / f"{digest}.npy"
        if path.exists():
            self.vectors = np.load(path)
            return
        log.info(f"Indexing {len(self.chunks)} passages")
        vectors = np.array(list(self.embedder.passage_embed([c.text for c in self.chunks])), dtype=np.float32)
        self.vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, self.vectors)

    def _rrf(self, query: str, depth: int) -> dict[int, float]:
        rankings = []
        bm25 = self.bm25.get_scores(_tokens(query))
        rankings.append([i for i in np.argsort(-bm25)[:depth] if bm25[i] > 0])
        if self.vectors is not None:
            q = next(iter(self.embedder.query_embed(query)))
            sims = self.vectors @ (q / np.linalg.norm(q))
            rankings.append(list(np.argsort(-sims)[:depth]))
        scores: dict[int, float] = defaultdict(float)
        for ranking in rankings:
            for rank, idx in enumerate(ranking):
                scores[int(idx)] += 1.0 / (RRF_K + rank + 1)
        return scores

    def search(self, queries: list[str], k_per_query: int, limit: int) -> list[Chunk]:
        """Fuse results across several queries and return them in document order."""
        total: dict[int, float] = defaultdict(float)
        for q in queries:
            for idx, score in sorted(self._rrf(q, k_per_query * 3).items(), key=lambda kv: -kv[1])[:k_per_query]:
                total[idx] += score
        top = sorted(total, key=total.get, reverse=True)[:limit]
        return [self.chunks[i] for i in sorted(top)]
