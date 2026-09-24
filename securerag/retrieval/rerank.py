"""Optional cross-encoder reranking.

A cross-encoder reads (query, passage) pairs, so it needs plaintext. The ``rerank`` graph node
therefore only scores candidates that already pass ``rbac.authorize`` for the caller, and keeps
the decrypted text only for the duration of the scoring call.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Protocol, Sequence

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder wrapper (default ``BAAI/bge-reranker-base``)."""

    def __init__(self, model_name: str, device: str = "auto", batch_size: int = 16):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model: Any = None

    @property
    def model(self) -> Any:
        if self._model is None:
            self._model = _load_cross_encoder(self.model_name, self.device)
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        scores = self.model.predict([(query, p) for p in passages], batch_size=self.batch_size,
                                    show_progress_bar=False)
        return [float(s) for s in scores]


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str, device: str) -> Any:
    from sentence_transformers import CrossEncoder

    from securerag.retrieval.embedder import resolve_device

    resolved = resolve_device(device)
    logger.info("Loading reranker %s on %s", model_name, resolved)
    return CrossEncoder(model_name, device=resolved)
