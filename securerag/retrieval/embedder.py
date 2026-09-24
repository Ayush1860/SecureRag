"""Shared embedding model and tokenizer.

The SentenceTransformer is loaded once per (model, device) and reused by ingestion and
retrieval; its tokenizer measures chunk length so chunks match what the model embeds.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Callable

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

logger = logging.getLogger(__name__)


def resolve_device(preference: str = "auto") -> str:
    if preference != "auto":
        return preference
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


@lru_cache(maxsize=4)
def get_encoder(model_name: str, device: str = "auto") -> Any:
    from sentence_transformers import SentenceTransformer

    resolved = resolve_device(device)
    logger.info("Loading embedding model %s on %s", model_name, resolved)
    return SentenceTransformer(model_name, device=resolved)


def token_length_fn(tokenizer: Any) -> Callable[[str], int]:
    """Length function counting model tokens (without special tokens)."""

    def length(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False, verbose=False))

    return length


def model_max_tokens(encoder: Any) -> int | None:
    """Largest chunk (in content tokens) the encoder embeds without truncation."""
    limit = getattr(encoder, "max_seq_length", None)
    return int(limit) - 2 if limit else None  # [CLS] + [SEP]
