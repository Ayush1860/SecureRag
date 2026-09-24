"""Ingest-time prompt-injection detection.

Every chunk is scanned once during ingestion and the verdict is stored as the
``injection_flagged`` metadata field; query time reuses that flag instead of re-scanning.
The regex heuristics in ``sanitizer`` always run. An optional Hugging Face text classifier
(``INJECTION_CLASSIFIER``, e.g. ``protectai/deberta-v3-base-prompt-injection-v2``) can be added
on top; it runs at ingest only, so it adds no query latency.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from securerag.security.sanitizer import flag_suspicious

logger = logging.getLogger(__name__)

Classifier = Callable[[list[str]], list[float]]  # texts -> probability of injection


class InjectionDetector:
    def __init__(self, classifier: Classifier | None = None, threshold: float = 0.5):
        self.classifier = classifier
        self.threshold = threshold

    def flags(self, texts: list[str]) -> list[bool]:
        regex = [bool(flag_suspicious(t)) for t in texts]
        if self.classifier is None or not texts:
            return regex
        scores = self.classifier(texts)
        return [r or s >= self.threshold for r, s in zip(regex, scores)]


def hf_classifier(model_name: str, positive_label: str = "INJECTION", device: str = "auto",
                  batch_size: int = 16) -> Classifier:
    """Wrap a transformers text-classification model as ``texts -> P(injection)``."""
    from transformers import pipeline

    from securerag.retrieval.embedder import resolve_device

    resolved = resolve_device(device)
    clf: Any = pipeline("text-classification", model=model_name, truncation=True,
                        device=0 if resolved == "cuda" else -1)
    logger.info("Loaded injection classifier %s on %s", model_name, resolved)

    def score(texts: list[str]) -> list[float]:
        out = []
        for result in clf(texts, batch_size=batch_size, top_k=None):
            by_label = {r["label"].upper(): r["score"] for r in result}
            out.append(float(by_label.get(positive_label.upper(), 0.0)))
        return out

    return score


def detector_from_settings(settings: Any) -> InjectionDetector:
    if not getattr(settings, "injection_classifier", ""):
        return InjectionDetector()
    return InjectionDetector(hf_classifier(settings.injection_classifier, settings.injection_label,
                                           settings.embed_device), settings.injection_threshold)
