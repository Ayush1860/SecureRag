import json
from pathlib import Path

import pytest

from securerag.pipeline.graph import SecureRAG
from securerag.retrieval import sparse as sparse_mod
from securerag.retrieval.store import StoreKeyError, StoreNotReadyError, open_serving_stack
from securerag.security.encryption import VectorStoreEncryptor
from securerag.security.rbac import authorize, build_chroma_filter

from conftest import SAMPLE_DOCS, FakeEncoder, ingest, make_settings, store_for, write_docs

PLAINTEXT_MARKERS = ["gross margin", "34.2", "240 crore", "Sentinel AMR", "paid leave"]


def _make_store(backend, settings):
    if backend == "chroma":
        return store_for(settings)
    pytest.importorskip("qdrant_client")
    from securerag.retrieval.qdrant_store import QdrantVectorStore

    return QdrantVectorStore(location=":memory:")


@pytest.fixture(params=["chroma", "qdrant"])
def backend_stack(request, tmp_path):
    settings = make_settings(tmp_path, vector_backend="chroma")
    write_docs(Path(settings.data_dir), SAMPLE_DOCS)
    encryptor = VectorStoreEncryptor()
    encoder = FakeEncoder()
    store = _make_store(request.param, settings)
    ingest(settings, encryptor, encoder, store=store)
    stack = open_serving_stack(settings, encryptor, store=store, encoder=encoder)
    engine = SecureRAG(stack.store, stack.retriever, encryptor, settings.audit_log_path)
    return {"backend": request.param, "settings": settings, "encryptor": encryptor, "stack": stack, "engine": engine}


# --------------------------------------------------------------------------- startup checks

def test_startup_with_wrong_key_fails_fast_without_rebuild(tmp_path):
    settings = make_settings(tmp_path)
    write_docs(Path(settings.data_dir), SAMPLE_DOCS)
    ingest(settings, VectorStoreEncryptor(), FakeEncoder())
    store = store_for(settings)
    before = sorted(cid for batch in store.iter_all() for cid, _, _ in batch)

    with pytest.raises(StoreKeyError, match="key"):
        open_serving_stack(settings, VectorStoreEncryptor(), store=store, encoder=FakeEncoder())
    after = sorted(cid for batch in store.iter_all() for cid, _, _ in batch)
    assert after == before  # nothing rebuilt or rewritten


def test_startup_detects_undecryptable_payloads(tmp_path):
    settings = make_settings(tmp_path)
    write_docs(Path(settings.data_dir), SAMPLE_DOCS)
    encryptor = VectorStoreEncryptor()
    ingest(settings, encryptor, FakeEncoder())
    store = store_for(settings)
    store.set_info({"index_key_id": ""})  # hide the key id so only the decrypt probe can catch it
    with pytest.raises(StoreKeyError, match="AES-GCM"):
        open_serving_stack(settings, VectorStoreEncryptor(), store=store, encoder=FakeEncoder(), )


def test_startup_on_empty_store_fails(tmp_path):
    settings = make_settings(tmp_path)
    with pytest.raises(StoreNotReadyError, match="empty"):
        open_serving_stack(settings, VectorStoreEncryptor(), store=store_for(settings), encoder=FakeEncoder())


def test_startup_refuses_embedding_model_mismatch(tmp_path):
    settings = make_settings(tmp_path)
    write_docs(Path(settings.data_dir), SAMPLE_DOCS)
    encryptor = VectorStoreEncryptor()
    ingest(settings, encryptor, FakeEncoder())
    other = settings.model_copy(update={"embed_model": "another-model"})
    with pytest.raises(StoreNotReadyError, match="embed_model"):
        open_serving_stack(other, encryptor, store=store_for(settings), encoder=FakeEncoder())


# --------------------------------------------------------------------------- no plaintext at rest in RAM

def _walk(obj, seen, depth=0):
    if id(obj) in seen or depth > 6:
        return
    seen.add(id(obj))
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, bytes):
        yield obj.decode("latin-1")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(k, seen, depth + 1)
            yield from _walk(v, seen, depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            yield from _walk(v, seen, depth + 1)
    elif hasattr(obj, "__dict__") and type(obj).__module__.startswith("securerag"):
        for v in vars(obj).values():
            yield from _walk(v, seen, depth + 1)


def test_no_plaintext_held_after_startup_and_queries(rag_stack):
    engine, stack = rag_stack["engine"], rag_stack["stack"]
    for role in ("guest", "finance_lead", "exec"):
        engine.query("What is the Q3 gross margin and revenue?", role, 3)
    strings = list(_walk([stack.retriever, stack.sparse, engine], set()))
    for marker in PLAINTEXT_MARKERS:
        assert not any(marker.lower() in s.lower() for s in strings), marker
    # The sparse vocabulary is keyed hashes, not words.
    for loaded in stack.sparse._cache.values():
        assert not {"gross", "margin", "revenue"} & set(loaded.vocab)


def test_sparse_index_on_disk_has_no_plaintext_vocabulary(rag_stack):
    sparse_dir = Path(rag_stack["settings"].resolved_sparse_dir)
    blob = "".join(p.read_text(encoding="latin-1") for p in sparse_dir.rglob("*") if p.is_file())
    for word in ("margin", "revenue", "onboarding", "lidar"):
        assert word not in blob.lower()


# --------------------------------------------------------------------------- backend parity

@pytest.mark.parametrize("role", ["guest", "employee", "finance_lead", "exec"])
def test_backends_enforce_rbac_and_find_authorized_answers(backend_stack, role):
    engine = backend_stack["engine"]
    res = engine.query("What is the Q3 gross margin?", role, 3)
    for h in res["retrieved"]:
        assert authorize(role, h.metadata), (backend_stack["backend"], h.metadata)
    if role in ("finance_lead", "exec"):
        assert "34.2%" in res["answer"]
    else:
        assert "34.2" not in res["answer"]
        assert all("34.2" not in e["text"] for e in res["context_excerpts"])


def test_backend_dense_prefilter_respects_where(backend_stack):
    store = backend_stack["stack"].store
    encoder = FakeEncoder()
    vec = encoder.encode(["gross margin cash runway"])[0].tolist()
    where = build_chroma_filter("guest")
    ids = [cid for cid, _ in store.query(vec, 10, where)]
    metas = dict(store.get_metadata(ids))
    assert metas and all(m["department"] == "general" and m["clearance"] == "public" for m in metas.values())


# --------------------------------------------------------------------------- sparse partitions

def test_guest_query_never_opens_confidential_partitions(rag_stack, monkeypatch):
    sparse = rag_stack["stack"].sparse
    sparse._cache.clear()
    opened = []
    real_load = sparse._load

    def spy(partition):
        opened.append(partition)
        return real_load(partition)

    monkeypatch.setattr(sparse, "_load", spy)
    rag_stack["engine"].query("Q3 gross margin cash runway confidential", "guest", 5)
    assert opened and set(opened) == {"general.public"}


def test_hmac_index_ranks_like_plaintext_bm25(tmp_path):
    import bm25s

    corpus = [SAMPLE_DOCS[k] for k in sorted(SAMPLE_DOCS)] + [
        "Margin pressure from LiDAR component costs hit Q3.",
        "The onboarding checklist covers laptops and badges.",
    ]
    ids = [f"c{i}" for i in range(len(corpus))]
    hasher = sparse_mod.TermHasher(b"k" * 32)
    sparse_mod.build_partition(tmp_path, "general.public", zip(ids, (hasher.encode_text(t) for t in corpus)))
    reader = sparse_mod.SparseRetriever(tmp_path, hasher)

    plain = bm25s.BM25()
    plain.index([sparse_mod.tokenize(t) for t in corpus], show_progress=False)
    for query in ("gross margin", "onboarding leave", "LiDAR vendors", "revenue crore"):
        hashed = reader.search(query, ["general.public"], k=3)
        scores = plain.get_scores(sparse_mod.tokenize(query))
        expected = sorted(((ids[i], s) for i, s in enumerate(scores) if s > 0), key=lambda x: -x[1])[:3]
        assert [c for c, _ in hashed] == [c for c, _ in expected]
        assert [round(s, 5) for _, s in hashed] == [round(float(s), 5) for _, s in expected]


def test_incremental_ingest_rebuilds_only_touched_partitions(tmp_path):
    settings = make_settings(tmp_path)
    write_docs(Path(settings.data_dir), SAMPLE_DOCS)
    encryptor = VectorStoreEncryptor()
    ingest(settings, encryptor, FakeEncoder())
    sparse_dir = Path(settings.resolved_sparse_dir)

    def versions():
        return {p.name: json.loads((p / "partition.json").read_text())["version"] for p in sparse_dir.iterdir()
                if (p / "partition.json").exists()}

    before = versions()
    write_docs(Path(settings.data_dir), {"hr/internal/onboarding_policy.txt": "Onboarding now lasts three weeks."})
    report = ingest(settings, encryptor, FakeEncoder())
    after = versions()
    assert report.sparse_partitions_rebuilt == 1
    assert after["hr.internal"] != before["hr.internal"]
    assert all(after[p] == before[p] for p in before if p != "hr.internal")


def test_deleting_last_doc_of_partition_removes_partition(tmp_path):
    settings = make_settings(tmp_path)
    write_docs(Path(settings.data_dir), SAMPLE_DOCS)
    encryptor = VectorStoreEncryptor()
    ingest(settings, encryptor, FakeEncoder())
    (Path(settings.data_dir) / "finance/confidential/q3_internal_memo.txt").unlink()
    ingest(settings, encryptor, FakeEncoder())
    assert not (Path(settings.resolved_sparse_dir) / "finance.confidential").exists()



def test_chroma_dense_query_uses_partition_prefilter(rag_stack, monkeypatch):
    store = rag_stack["stack"].store
    seen = []
    real = store.collection.query

    def spy(**kw):
        seen.append(kw.get("where"))
        return real(**kw)

    monkeypatch.setattr(store.collection, "query", spy)
    rag_stack["engine"].query("Q3 gross margin cash runway", "guest", 5)
    assert seen == [{"partition": "general.public"}]
