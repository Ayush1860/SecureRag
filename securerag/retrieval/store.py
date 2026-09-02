import os
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import logging
import uuid
from pathlib import Path
import chromadb
from sentence_transformers import SentenceTransformer

from securerag.ingestion.loader import load_tagged_documents, chunk_text
from securerag.retrieval.hybrid import Chunk
from securerag.security.encryption import VectorStoreEncryptor, DecryptionError

logger = logging.getLogger(__name__)

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION_NAME = "securerag_chunks"


def build_store(data_dir: str, chroma_dir: str, encryptor: VectorStoreEncryptor):
    """
    Ingests raw documents from data_dir, chunks them, encrypts payload text with AES-256-GCM,
    generates dense embeddings, and stores them in a persistent ChromaDB collection.
    """
    docs = list(load_tagged_documents(data_dir))
    rows = []
    for text, meta in docs:
        for piece in chunk_text(text):
            rows.append((piece, meta))

    encoder = SentenceTransformer(EMBED_MODEL)
    embeddings = encoder.encode([r[0] for r in rows], normalize_embeddings=True, convert_to_numpy=True)

    Path(chroma_dir).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_dir)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.get_or_create_collection(COLLECTION_NAME)

    ids, payloads, metas = [], [], []
    for text, meta in rows:
        ids.append(str(uuid.uuid4()))
        payloads.append(encryptor.encrypt(text))
        metas.append(meta)

    collection.add(ids=ids, embeddings=embeddings.tolist(), documents=payloads, metadatas=metas)

    chunks = [Chunk(id=i, encrypted_text=e, text=t, metadata=m) for i, e, (t, m) in zip(ids, payloads, rows)]
    return client, collection, encoder, chunks


def load_store(chroma_dir: str, data_dir: str, encryptor: VectorStoreEncryptor):
    """
    Loads persistent ChromaDB collection and verifies that encrypted payloads can be authenticated.
    If empty or if key mismatch is detected, re-indexes the corpus.
    """
    client = chromadb.PersistentClient(path=chroma_dir)
    collection = client.get_or_create_collection(COLLECTION_NAME)
    if collection.count() == 0:
        return build_store(data_dir, chroma_dir, encryptor)

    encoder = SentenceTransformer(EMBED_MODEL)
    chunks = []
    data = collection.get(include=["documents", "metadatas"])

    try:
        for cid, enc, meta in zip(data["ids"], data["documents"], data["metadatas"]):
            decrypted = encryptor.decrypt(enc)
            chunks.append(Chunk(id=cid, encrypted_text=enc, text=decrypted, metadata=meta))
    except DecryptionError as exc:
        logger.warning(
            "Encrypted payloads in %s cannot be authenticated with current AES key (%s). "
            "Rebuilding store with active key.",
            chroma_dir,
            exc,
        )
        return build_store(data_dir, chroma_dir, encryptor)

    return client, collection, encoder, chunks
