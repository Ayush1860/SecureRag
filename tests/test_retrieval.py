import pytest

from securerag.retrieval.hybrid import compute_rrf, matches_filter
from securerag.retrieval.sparse import partitions_for_filter
from securerag.security.rbac import build_chroma_filter


def test_compute_rrf_scoring():
    rankings = [
        ["docA", "docB", "docC"],
        ["docB", "docA", "docD"],
    ]
    scores = compute_rrf(rankings, k=60)
    # docA: 1/(61) + 1/(62) = 0.016393 + 0.016129 = 0.032522
    # docB: 1/(62) + 1/(61) = 0.032522
    # docC: 1/(63) = 0.015873
    # docD: 1/(62) = 0.016129
    assert scores["docA"] == pytest.approx(scores["docB"])
    assert scores["docA"] > scores["docC"]
    assert scores["docA"] > scores["docD"]


def test_hybrid_retriever_filtering(rag_stack):
    retriever = rag_stack["stack"].retriever
    where_filter = {
        "$and": [
            {"department": {"$in": ["general"]}},
            {"clearance": {"$in": ["public"]}},
        ]
    }
    results = retriever.retrieve("sensor LiDAR revenue margin", top_k=5, where=where_filter)
    assert len(results) >= 1
    assert all(r.metadata["department"] == "general" for r in results)
    assert all(r.metadata["clearance"] == "public" for r in results)


@pytest.mark.parametrize("role", ["guest", "employee", "finance_lead", "exec"])
def test_retrieval_never_returns_unauthorized_chunks(rag_stack, role):
    retriever = rag_stack["stack"].retriever
    where = build_chroma_filter(role)
    for query in ("Q3 gross margin cash runway", "revenue", "onboarding leave", "navigation stack", "LiDAR"):
        for chunk in retriever.retrieve(query, top_k=10, where=where):
            assert matches_filter(chunk.metadata, where)


def test_matches_filter_fails_closed_on_unknown_operator():
    assert not matches_filter({"department": "general"}, {"department": {"$nin": ["hr"]}})
    assert matches_filter({"department": "general"}, {"department": "general"})


def test_partitions_for_filter_matches_role_policy():
    assert partitions_for_filter(build_chroma_filter("guest")) == ["general.public"]
    fin = set(partitions_for_filter(build_chroma_filter("finance_lead")))
    assert "finance.confidential" in fin and not any(p.startswith("hr.") for p in fin)
    assert partitions_for_filter({"$or": [{"department": "hr"}]}) == []  # not understood -> nothing
