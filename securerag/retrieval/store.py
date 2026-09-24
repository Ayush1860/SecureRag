"""Opening the encrypted store for ingestion and for serving.

Serving never loads or decrypts the corpus. Startup opens the vector store, checks that it was
built with the configured embedding model and key, proves the key by decrypting a small random
sample, and opens the sparse index lazily. A wrong key is a hard startup error; the only way to
re-encrypt is an explicit ``scripts/ingest.py --full-rebuild`` (or key rotation, Phase 4).
"""
import os

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import logging
from dataclasses import dataclass
from typing import Any

from securerag.config import Settings
from securerag.ingestion.pipeline import IngestPipeline, IngestReport, ProgressFn
from securerag.ingestion.state import IngestState
from securerag.retrieval.embedder import get_encoder
from securerag.retrieval.hybrid import HybridRetriever
from securerag.retrieval.sparse import SPARSE_KEY_LABEL, SparseRetriever, TermHasher
from securerag.retrieval.vector_store import COLLECTION_NAME, ChromaVectorStore, VectorStore
from securerag.security.encryption import INDEX_KEY_LABEL, DecryptionError, VectorStoreEncryptor, aad_for

logger = logging.getLogger(__name__)

KEY_CHECK_SAMPLE = 5


class StoreNotReadyError(RuntimeError):
    """The store is empty or was built for a different configuration."""


class StoreKeyError(RuntimeError):
    """The configured AES key cannot decrypt the store."""


def open_vector_store(settings: Settings) -> VectorStore:
    if settings.vector_backend == "qdrant":
        from securerag.retrieval.qdrant_store import QdrantVectorStore

        if settings.qdrant_url:
            return QdrantVectorStore(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None,
                                     collection_name=COLLECTION_NAME)
        return QdrantVectorStore(path=settings.qdrant_path, collection_name=COLLECTION_NAME)
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
    store: VectorStore | None = None,
    run_id: str | None = None,
) -> IngestReport:
    """Run the streaming ingestion pipeline against the configured store."""
    encoder = encoder or get_encoder(settings.embed_model, settings.embed_device)
    if store is None and not dry_run:
        store = open_vector_store(settings)
    state = IngestState(settings.resolved_state_db_path)
    try:
        from securerag.security.injection import detector_from_settings

        pipeline = IngestPipeline(settings, encryptor, store, state, encoder=encoder,
                                  sparse_dir=settings.resolved_sparse_dir,
                                  injection_detector=detector_from_settings(settings))
        return pipeline.run(data_dir or settings.data_dir, full_rebuild=full_rebuild, dry_run=dry_run,
                            workers=workers, progress=progress, run_id=run_id)
    finally:
        state.close()


def verify_store(store: VectorStore, encryptor: VectorStoreEncryptor, settings: Settings,
                 sample: int = KEY_CHECK_SAMPLE) -> None:
    """Fail fast if the store is empty, built for another model/key, or not decryptable."""
    count = store.count()
    if count == 0:
        raise StoreNotReadyError("vector store is empty; run `python scripts/ingest.py` first")
    info = store.get_info()
    if info.get("embed_model") and info["embed_model"] != settings.embed_model:
        raise StoreNotReadyError(
            f"store was built with embed_model={info['embed_model']!r} but EMBED_MODEL={settings.embed_model!r}; "
            "query vectors would be meaningless. Re-ingest with --full-rebuild or restore the setting.")
    if info.get("index_key_id") and info["index_key_id"] != encryptor.index_key_id:
        raise StoreKeyError(
            f"store was indexed under key {info['index_key_id']} but the configured index key is "
            f"{encryptor.index_key_id}. Configure the original key (or keyring), or re-ingest with --full-rebuild.")
    for cid, ciphertext, meta in store.get(store.sample_ids(min(sample, count))):
        try:
            encryptor.decrypt(ciphertext, aad=aad_for(cid, meta))
        except DecryptionError as exc:
            raise StoreKeyError(
                f"chunk {cid} failed AES-GCM authentication with the configured key. Either the key is wrong or "
                "the store was tampered with. Refusing to start; no automatic rebuild is attempted.") from exc


def build_engine(settings: Settings, encryptor: VectorStoreEncryptor, stack: "ServingStack",
                 audit_path: str | None = None, reranker: Any = None) -> Any:
    """SecureRAG pipeline over ``stack`` configured from settings (rerank, context budget)."""
    from securerag.pipeline.graph import SecureRAG

    if reranker is None and settings.rerank_enabled:
        from securerag.retrieval.rerank import CrossEncoderReranker

        reranker = CrossEncoderReranker(settings.rerank_model, settings.embed_device)
    from securerag.llm.router import LLMRouter

    return SecureRAG(stack.store, stack.retriever, encryptor, audit_path or settings.audit_log_path,
                     reranker=reranker, rerank_top_n=settings.rerank_top_n,
                     context_token_budget=settings.context_token_budget, llm=LLMRouter.from_env(settings))


@dataclass
class ServingStack:
    store: VectorStore
    encoder: Any
    sparse: SparseRetriever
    retriever: HybridRetriever


def open_serving_stack(settings: Settings, encryptor: VectorStoreEncryptor, *, store: VectorStore | None = None,
                       encoder: Any = None) -> ServingStack:
    store = store or open_vector_store(settings)
    verify_store(store, encryptor, settings)
    encoder = encoder or get_encoder(settings.embed_model, settings.embed_device)
    sparse = SparseRetriever(settings.resolved_sparse_dir, TermHasher(encryptor.derive_subkey(SPARSE_KEY_LABEL)))
    if not sparse.available_partitions():
        logger.warning("no sparse index found under %s; retrieval runs dense-only until the next ingest",
                       settings.resolved_sparse_dir)
    retriever = HybridRetriever(store, encoder, sparse, fusion_k=settings.fusion_k,
                                dense_candidates=settings.dense_candidates,
                                sparse_candidates=settings.sparse_candidates,
                                query_prefix=settings.embed_query_prefix)
    logger.info("serving stack ready: backend=%s chunks=%d partitions=%d", store.backend, store.count(),
                len(sparse.available_partitions()))
    return ServingStack(store=store, encoder=encoder, sparse=sparse, retriever=retriever)


__all__ = ["build_engine", "COLLECTION_NAME", "INDEX_KEY_LABEL", "ServingStack", "StoreKeyError", "StoreNotReadyError",
           "open_serving_stack", "open_vector_store", "run_ingestion", "verify_store"]
