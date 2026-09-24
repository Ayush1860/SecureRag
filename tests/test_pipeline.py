import pytest


@pytest.fixture
def mock_pipeline(rag_stack):
    return rag_stack["engine"]


def test_pipeline_guest_query(mock_pipeline):
    # Guest querying public data
    res = mock_pipeline.query("What is the revenue?", user_role="guest", top_k=2)
    assert res["user_role"] == "guest"
    assert "request_id" in res
    assert res["authorized"]
    assert res["answer"]
    assert "240 crore" in res["answer"]


def test_pipeline_guest_blocked_from_confidential(mock_pipeline):
    # Guest querying confidential finance info
    res = mock_pipeline.query("What is the Q3 margin?", user_role="guest", top_k=2)
    # The retriever filter excludes finance, so no chunks are retrieved or authorized
    assert all(h.metadata["clearance"] == "public" for h in res.get("authorized", []))
    assert "34.2" not in res["answer"]


def test_pipeline_finance_lead_can_access_confidential(mock_pipeline):
    # Finance lead querying financial info
    res = mock_pipeline.query("What is the Q3 margin?", user_role="finance_lead", top_k=2)
    assert res["user_role"] == "finance_lead"
    assert any(h.metadata["clearance"] == "confidential" for h in res["authorized"])
    assert "34.2%" in res["answer"]


def test_pipeline_indirect_injection_sanitization(mock_pipeline):
    # Query retrieving the poisoned vendor feedback document
    res = mock_pipeline.query("LiDAR feedback from vendors", user_role="employee", top_k=2)
    assert res["flagged_count"] > 0
    # Confirm untrusted wrapper was used
    assert "[UNTRUSTED_DOCUMENT_CONTENT" in res["context_block"]
    # Confirm mock LLM grounded itself on facts and did not follow the injection instruction
    assert "Ignore all previous instructions" not in res["answer"]


def test_pipeline_empty_query_rejected(mock_pipeline):
    with pytest.raises(ValueError, match="Query cannot be empty"):
        mock_pipeline.query("   ", user_role="employee")


def test_retrieved_chunks_carry_no_plaintext(mock_pipeline):
    res = mock_pipeline.query("What is the Q3 margin?", user_role="finance_lead", top_k=2)
    for chunk in res["retrieved"]:
        assert set(vars(chunk)) == {"id", "metadata", "score"}
