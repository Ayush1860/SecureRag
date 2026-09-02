import pytest
from unittest.mock import MagicMock
from securerag.retrieval.hybrid import Chunk, HybridRetriever, compute_rrf


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


def test_hybrid_retriever_filtering():
    chunks = [
        Chunk(id="c1", encrypted_text="enc1", text="LiDAR warehouse sensor", metadata={"department": "general", "clearance": "public"}),
        Chunk(id="c2", encrypted_text="enc2", text="Financial margin report", metadata={"department": "finance", "clearance": "confidential"}),
        Chunk(id="c3", encrypted_text="enc3", text="Engineering robotics specs", metadata={"department": "engineering", "clearance": "internal"}),
    ]

    mock_collection = MagicMock()
    mock_collection.count.return_value = 3
    mock_collection.query.return_value = {"ids": [["c1", "c2"]]}

    mock_encoder = MagicMock()
    mock_encoder.encode.return_value = MagicMock(tolist=lambda: [[0.1, 0.2]])

    retriever = HybridRetriever(mock_collection, mock_encoder, chunks)

    # Filter allowing only general / public
    where_filter = {
        "$and": [
            {"department": {"$in": ["general"]}},
            {"clearance": {"$in": ["public"]}},
        ]
    }

    filtered = retriever._filter_allowed_chunks(where_filter)
    assert len(filtered) == 1
    assert filtered[0].id == "c1"

    # Hybrid retrieve
    results = retriever.retrieve("sensor", top_k=2, where=where_filter)
    assert len(results) >= 1
    assert all(r.metadata["department"] == "general" for r in results)
