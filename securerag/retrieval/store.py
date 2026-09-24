import os
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import logging
from typing import Any

from securerag.config import Settings, get_settings
from securerag.ingestion.pipeline import IngestPipeline, IngestReport, ProgressFn
from securerag.ingestion.state import IngestState
from securerag.retrieval.embedder import get_encoder
from securerag.retrieval.hybrid import Chunk
from securerag.retrieval.vector_store import COLLECTION_NAME, ChromaVectorStore
from securerag.security.encryption import VectorStoreEncryptor, DecryptionError

logger = logging.getLogger(__name__)

__all__ = ["COLLECTION_NAME", "build_store", "load_store", "open_vector_store", "run_ingestion"]


def _settings_for(data_dir: str | None = None, chroma_dir: str | None = None) -> Settings:
    settings = get_settings()
    update = {k: v for k, v in {"data_dir": data_dir, "chroma_dir": chroma_dir}.items() if v is not None}
    return settings.model_copy(update=update) if update else settings


def open_vector_store(settings: Settings) -> ChromaVectorStore:
    return ChromaVectorStore(settings.chroma_dir)


def run_ingestion(
    settings: Settings,
    encryptor: VectorStoreEncryptor,
    *,
    data_dir: str | None = None,
    full_rebuild: bool = False,
    dry_run: bool = False,
    workers: int = 1,
    progress: ProgressFn | None = None,
    encoder: Any = None,
) -> IngestReport:
    """Run the streaming ingestion pipeline against the configured store."""
    encoder = encoder or get_encoder(settings.embed_model, settings.embed_device)
    store = None if dry_run else open_vector_store(settings)
    state = IngestState(settings.resolved_state_db_path)
    try:
        pipeline = IngestPipeline(settings, encryptor, store, state, encoder=encoder)
        return pipeline.run(data_dir or settings.data_dir, full_rebuild=full_rebuild, dry_run=dry_run,
                            workers=workers, progress=progress)
    finally:
        state.close()


def build_store(data_dir: str, chroma_dir: str, encryptor: VectorStoreEncryptor):
    """
    Rebuilds the store from data_dir (full rebuild) and returns (client, collection, encoder, chunks).
    Kept for callers of the original API; new code should use ``run_ingestion``.
    """
    settings = _settings_for(data_dir, chroma_dir)
    run_ingestion(settings, encryptor, full_rebuild=True)
    return load_store(chroma_dir, data_dir, encryptor)


def load_store(chroma_dir: str, data_dir: str, encryptor: VectorStoreEncryptor):
    """
    Loads persistent ChromaDB collection and verifies that encrypted payloads can be authenticated.
    If empty or if key mismatch is detected, re-indexes the corpus.
    """
    settings = _settings_for(data_dir, chroma_dir)
    store = open_vector_store(settings)
    if store.count() == 0:
        return build_store(data_dir, chroma_dir, encryptor)

    encoder = get_encoder(settings.embed_model, settings.embed_device)
    chunks = []
    try:
        for batch in store.iter_all():
            for cid, enc, meta in batch:
                chunks.append(Chunk(id=cid, encrypted_text=enc, text=encryptor.decrypt(enc), metadata=meta))
    except DecryptionError as exc:
        logger.warning(
            "Encrypted payloads in %s cannot be authenticated with current AES key (%s). "
            "Rebuilding store with active key.",
            chroma_dir,
            exc,
        )
        return build_store(data_dir, chroma_dir, encryptor)

    return store.client, store.collection, encoder, chunks
