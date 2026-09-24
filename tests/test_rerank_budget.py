from pathlib import Path

from securerag.pipeline.graph import SecureRAG, approx_tokens
from securerag.retrieval.hybrid import Chunk
from securerag.retrieval.store import open_serving_stack

from conftest import SAMPLE_DOCS, FakeEncoder, ingest, make_settings, store_for, write_docs


class KeywordReranker:
    """Scores passages by how often ``keyword`` appears; records what it was shown."""

    def __init__(self, keyword: str):
        self.keyword = keyword
        self.seen: list[str] = []

    def score(self, query, passages):
        self.seen.extend(passages)
        return [p.lower().count(self.keyword) for p in passages]


class CountingEncryptor:
    def __init__(self, inner):
        self.inner = inner
        self.decrypted: list[str] = []

    def decrypt(self, blob):
        pt = self.inner.decrypt(blob)
        self.decrypted.append(pt)
        return pt

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _engine(rag_stack, **kw):
    stack = rag_stack["stack"]
    enc = CountingEncryptor(rag_stack["encryptor"])
    return SecureRAG(stack.store, stack.retriever, enc, rag_stack["settings"].audit_log_path, **kw), enc


def test_rerank_node_is_skipped_when_disabled(rag_stack):
    engine, _ = _engine(rag_stack)
    res = engine.query("revenue LiDAR onboarding", "exec", 3)
    assert res["reranked"] is False


def test_rerank_reorders_and_cuts_to_top_k(rag_stack):
    reranker = KeywordReranker("onboarding")
    engine, _ = _engine(rag_stack, reranker=reranker, rerank_top_n=10)
    res = engine.query("revenue LiDAR onboarding margin navigation", "exec", 2)
    assert res["reranked"] is True
    assert len(res["authorized"]) <= 2
    assert "onboarding" in res["authorized"][0].metadata["source"]
    assert len(reranker.seen) > 2  # it scored the wider candidate pool, not just top_k


def test_rerank_never_decrypts_unauthorized_candidates(rag_stack):
    stack = rag_stack["stack"]
    finance_ids = [cid for batch in stack.store.iter_all() for cid, _, m in batch if m["department"] == "finance"]
    finance_meta = dict(stack.store.get_metadata(finance_ids))
    real_retrieve = stack.retriever.retrieve

    def leaky_retrieve(*a, **kw):  # simulate a retriever bug that lets a confidential chunk through
        return [Chunk(id=cid, metadata=finance_meta[cid]) for cid in finance_ids] + real_retrieve(*a, **kw)

    stack.retriever.retrieve = leaky_retrieve
    reranker = KeywordReranker("margin")
    engine, enc = _engine(rag_stack, reranker=reranker)
    res = engine.query("What is the Q3 gross margin?", "guest", 3)
    assert res["blocked_count"] == len(finance_ids)
    assert all("34.2" not in p for p in reranker.seen + enc.decrypted)
    assert "34.2" not in res["answer"]


def test_context_budget_drops_low_ranked_chunks_without_decrypting_them(tmp_path):
    settings = make_settings(tmp_path, chunk_size_tokens=20, chunk_overlap_tokens=0)
    long_doc = " ".join(f"Robot fleet note {i} mentions warehouse docking and battery swaps." for i in range(12))
    write_docs(Path(settings.data_dir), {"general/public/fleet.txt": long_doc})
    encoder = FakeEncoder()
    from securerag.security.encryption import VectorStoreEncryptor

    encryptor = VectorStoreEncryptor()
    ingest(settings, encryptor, encoder)
    stack = open_serving_stack(settings, encryptor, store=store_for(settings), encoder=encoder)
    counting = CountingEncryptor(encryptor)
    engine = SecureRAG(stack.store, stack.retriever, counting, settings.audit_log_path, context_token_budget=30)
    res = engine.query("warehouse docking battery", "guest", 5)
    assert len(res["authorized"]) == 5
    assert res["dropped_for_budget"] > 0
    kept = len(res["context_excerpts"])
    assert kept == 5 - res["dropped_for_budget"]
    assert len(counting.decrypted) == kept  # dropped chunks were never decrypted
    assert sum(len(e["text"].split()) for e in res["context_excerpts"]) <= 30 or kept == 1


def test_top_chunk_always_kept_even_if_over_budget(rag_stack):
    engine, _ = _engine(rag_stack, context_token_budget=1)
    res = engine.query("revenue", "guest", 3)
    assert len(res["context_excerpts"]) == 1


def test_embedding_prefixes_are_applied(tmp_path):
    class RecordingEncoder(FakeEncoder):
        def __init__(self):
            super().__init__()
            self.texts = []

        def encode(self, texts, **kw):
            self.texts.extend(texts)
            return super().encode(texts, **kw)

    settings = make_settings(tmp_path, embed_query_prefix="query: ", embed_doc_prefix="passage: ")
    write_docs(Path(settings.data_dir), SAMPLE_DOCS)
    from securerag.security.encryption import VectorStoreEncryptor

    encryptor = VectorStoreEncryptor()
    encoder = RecordingEncoder()
    ingest(settings, encryptor, encoder)
    assert encoder.texts and all(t.startswith("passage: ") for t in encoder.texts)
    encoder.texts.clear()
    stack = open_serving_stack(settings, encryptor, store=store_for(settings), encoder=encoder)
    stack.retriever.retrieve("revenue", 3)
    assert encoder.texts == ["query: revenue"]


def test_approx_tokens():
    assert approx_tokens("") == 1
    assert approx_tokens("a" * 40) == 10
