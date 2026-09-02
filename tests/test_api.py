import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from app.api import app, state
from securerag.retrieval.hybrid import Chunk, HybridRetriever
from securerag.security.encryption import VectorStoreEncryptor
from securerag.pipeline.graph import SecureRAG


@pytest.fixture
def client_with_mock_state(tmp_path):
    encryptor = VectorStoreEncryptor()
    text = "Acme Robotics operates in Bengaluru."
    c1 = Chunk(id="c1", encrypted_text=encryptor.encrypt(text), text=text, metadata={"department": "general", "clearance": "public", "source": "company_overview.txt"})

    collection = MagicMock()
    collection.count.return_value = 1
    collection.query.return_value = {"ids": [["c1"]]}

    encoder = MagicMock()
    encoder.encode.return_value = MagicMock(tolist=lambda: [[0.1, 0.2]])

    retriever = HybridRetriever(collection, encoder, [c1])
    audit_path = str(tmp_path / "api_audit.jsonl")
    engine = SecureRAG(collection, retriever, encryptor, audit_path)

    state["collection"] = collection
    state["engine"] = engine
    state["encryptor"] = encryptor
    state["settings"] = MagicMock(audit_log_path=audit_path)

    with TestClient(app) as client:
        yield client

    state.clear()


def test_health_endpoint(client_with_mock_state):
    res = client_with_mock_state.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "guest" in data["roles"]


def test_roles_endpoint(client_with_mock_state):
    res = client_with_mock_state.get("/api/roles")
    assert res.status_code == 200
    data = res.json()
    assert "finance_lead" in data


def test_query_endpoint_success(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "Where is company located?", "role": "employee", "top_k": 2})
    assert res.status_code == 200
    data = res.json()
    assert "request_id" in data
    assert data["role"] == "employee"
    assert "answer" in data
    assert "context_excerpts" in data


def test_query_endpoint_invalid_role(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "hello", "role": "hacker", "top_k": 2})
    assert res.status_code == 403


def test_query_endpoint_empty_query(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "", "role": "guest", "top_k": 2})
    assert res.status_code == 422  # pydantic min_length=1 validation error


def test_root_serves_html(client_with_mock_state):
    res = client_with_mock_state.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "SecureRAG" in res.text


def test_audit_endpoint(client_with_mock_state):
    # Run a query first to produce an audit record
    client_with_mock_state.post("/api/query", json={"query": "test audit query", "role": "employee", "top_k": 2})
    res = client_with_mock_state.get("/api/audit?limit=5")
    assert res.status_code == 200
    logs = res.json()
    assert isinstance(logs, list)
    assert len(logs) >= 1
    assert "query_hash" in logs[0]
