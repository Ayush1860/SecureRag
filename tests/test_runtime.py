"""Phase 5: LLM router (retries/fallbacks), SSE streaming, readiness, admin ingestion jobs,
structured logging."""
import json
import logging
import threading
from pathlib import Path

import pytest
from conftest import FakeEncoder
from fastapi.testclient import TestClient

from app.api import app, state
from securerag.ingestion.jobs import IngestJobManager
from securerag.llm.router import REFUSAL_TEXT, LLMRouter, ProviderUnavailable, is_retryable
from securerag.logging_config import configure_logging, request_id_var
from securerag.security.audit import read_recent_audit_events, verify_chain
from securerag.security.auth import Authenticator


class StatusError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


def scripted(plan):
    """Provider callable that raises/returns according to ``plan[provider]`` (a list consumed in order)."""
    calls = []

    def call(provider, system, user, timeout):
        calls.append(provider)
        step = plan[provider].pop(0) if plan[provider] else plan[provider + ":default"]
        if isinstance(step, BaseException):
            raise step
        return step

    return call, calls


def _router(chain, plan, retries=3):
    call, calls = scripted(plan)
    return LLMRouter(chain, max_retries=retries, backoff_base=0, backoff_max=0, call=call), calls


# --------------------------------------------------------------------------- router

def test_router_retries_retryable_errors_then_succeeds():
    router, calls = _router(["groq"], {"groq": [StatusError(429), StatusError(503), "answer"]})
    result = router.generate("s", "u")
    assert (result.text, result.provider, result.attempts, result.fallbacks) == ("answer", "groq", 3, [])


def test_router_falls_back_after_exhausting_retries():
    router, calls = _router(["groq", "gemini"], {"groq": [StatusError(500)] * 3, "gemini": ["from gemini"]})
    result = router.generate("s", "u")
    assert result.provider == "gemini" and result.fallbacks == ["groq"] and calls.count("groq") == 3


def test_router_does_not_retry_client_errors_or_missing_keys():
    router, calls = _router(["openai", "anthropic", "gemini"], {
        "openai": [StatusError(401)], "anthropic": [ProviderUnavailable("no key")], "gemini": ["ok"]})
    result = router.generate("s", "u")
    assert result.provider == "gemini" and calls == ["openai", "anthropic", "gemini"]


def test_router_refuses_when_everything_fails():
    router, _ = _router(["groq"], {"groq": [TimeoutError()] * 3})
    result = router.generate("s", "u")
    assert result.text == REFUSAL_TEXT and result.provider == "refusal" and result.fallbacks == ["groq"]


def test_router_stream_matches_generate():
    router, _ = _router(["mock"], {"mock": ["alpha beta  gamma\ndelta"] * 2})
    assert "".join(router.stream("s", "u")) == "alpha beta  gamma\ndelta"
    assert router.last.provider == "mock"


def test_router_from_env_chain(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_FALLBACKS", "gemini, groq ,refusal")
    assert LLMRouter.from_env().chain == ["groq", "gemini", "refusal"]


def test_missing_api_key_is_unavailable_not_retried(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    router = LLMRouter(["groq", "refusal"], backoff_base=0)
    result = router.generate("s", "u")
    assert result.provider == "refusal" and result.fallbacks == ["groq"]


@pytest.mark.parametrize("exc, expected", [
    (StatusError(429), True), (StatusError(502), True), (StatusError(400), False), (StatusError(403), False),
    (TimeoutError(), True), (ConnectionError(), True), (ValueError("bad"), False),
])
def test_is_retryable(exc, expected):
    assert is_retryable(exc) is expected


def test_graph_records_provider_and_attempts_in_state_and_audit(rag_stack):
    engine = rag_stack["engine"]
    engine.llm, _ = _router(["groq", "mock"], {"groq": [StatusError(503)] * 3, "mock": ["grounded answer"]})
    res = engine.query("What is the revenue?", "guest", 2)
    assert (res["llm_provider"], res["llm_attempts"], res["llm_fallbacks"]) == ("mock", 1, ["groq"])
    event = read_recent_audit_events(rag_stack["settings"].audit_log_path, limit=1)[0]
    assert event["provider"] == "mock" and event["llm_fallbacks"] == ["groq"]


# --------------------------------------------------------------------------- API: stream, ready, admin

def _client(rag_stack, **extra):
    settings = rag_stack["settings"].model_copy(update={"env": "dev", "auth_mode": "dev", "log_format": "text"})
    state.clear()
    state.update({"settings": settings, "authenticator": Authenticator(settings),
                  "store": rag_stack["stack"].store, "engine": rag_stack["engine"],
                  "encryptor": rag_stack["encryptor"], **extra})
    return TestClient(app, raise_server_exceptions=False)


def _parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_stream_endpoint_emits_meta_tokens_done_and_audits_after(rag_stack):
    with _client(rag_stack) as client:
        plain = client.post("/api/query", json={"query": "What is the Q3 gross margin?"},
                            headers={"X-Dev-Role": "finance_lead"}).json()
        res = client.post("/api/query/stream", json={"query": "What is the Q3 gross margin?"},
                          headers={"X-Dev-Role": "finance_lead"})
    state.clear()
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(res.text)
    assert events[0][0] == "meta" and events[-1][0] == "done"
    assert "".join(d["text"] for e, d in events if e == "token") == plain["answer"]
    assert events[0][1]["authorized"] == plain["authorized"]
    last = read_recent_audit_events(rag_stack["settings"].audit_log_path, limit=1)[0]
    assert last["streamed"] is True and last["request_id"] == events[0][1]["request_id"]
    assert verify_chain(rag_stack["settings"].audit_log_path)["ok"]


def test_stream_endpoint_enforces_auth_and_rbac(rag_stack):
    with _client(rag_stack) as client:
        assert client.post("/api/query/stream", json={"query": "x", "role": "exec"},
                           headers={"X-Dev-Role": "guest"}).status_code == 422
        res = client.post("/api/query/stream", json={"query": "What is the Q3 gross margin?"},
                          headers={"X-Dev-Role": "guest"})
    state.clear()
    assert "34.2" not in res.text


def test_ready_endpoint(rag_stack):
    with _client(rag_stack, ready_checks={"store_verified": True, "embedder_warm": True,
                                          "sparse_partitions": 3}) as client:
        ok = client.get("/api/ready")
    state.clear()
    assert ok.status_code == 200 and ok.json()["ready"] is True
    with _client(rag_stack) as client:  # startup checks never ran
        not_ready = client.get("/api/ready")
    state.clear()
    assert not_ready.status_code == 503


def test_admin_ingest_job_lifecycle(rag_stack):
    s = rag_stack["settings"]
    jobs = IngestJobManager(s, rag_stack["encryptor"], rag_stack["stack"].store, FakeEncoder())
    Path(s.data_dir, "hr/internal/new_policy.txt").write_text("Hybrid work is allowed three days per week.",
                                                              encoding="utf-8")
    with _client(rag_stack, jobs=jobs) as client:
        assert client.post("/api/admin/ingest", json={}, headers={"X-Dev-Role": "exec"}).status_code == 403
        assert client.post("/api/admin/ingest", json={"data_dir": "C:/Windows"},
                           headers={"X-Dev-Role": "admin"}).status_code == 422
        started = client.post("/api/admin/ingest", json={}, headers={"X-Dev-Role": "admin"})
        assert started.status_code == 202
        job_id = started.json()["job_id"]
        jobs.wait(30)
        status = client.get(f"/api/admin/ingest/{job_id}", headers={"X-Dev-Role": "admin"}).json()
        assert status["status"] == "completed" and status["stats"]["indexed_new"] == 1
        assert client.get("/api/admin/ingest/not-a-job", headers={"X-Dev-Role": "admin"}).status_code == 404
        # The serving engine sees the new document without a restart.
        res = client.post("/api/query", json={"query": "hybrid work days per week"},
                          headers={"X-Dev-Role": "employee"}).json()
        assert "new_policy.txt" in res["sources"]
    state.clear()
    events = read_recent_audit_events(s.audit_log_path, limit=20)
    assert any(e.get("event") == "admin_ingest_started" and e["principal_id"] == "dev:admin" for e in events)


def test_admin_ingest_rejects_concurrent_jobs(rag_stack, monkeypatch):
    jobs = IngestJobManager(rag_stack["settings"], rag_stack["encryptor"], rag_stack["stack"].store, FakeEncoder())
    gate = threading.Event()
    monkeypatch.setattr(jobs, "_run", lambda job_id, full: gate.wait(10))
    with _client(rag_stack, jobs=jobs) as client:
        first = client.post("/api/admin/ingest", json={}, headers={"X-Dev-Role": "admin"})
        second = client.post("/api/admin/ingest", json={}, headers={"X-Dev-Role": "admin"})
        gate.set()
    state.clear()
    assert first.status_code == 202 and second.status_code == 409


# --------------------------------------------------------------------------- logging

def test_json_logs_carry_request_id(capsys):
    configure_logging("json", "INFO")
    token = request_id_var.set("req-123")
    try:
        logging.getLogger("securerag.test").info("hello %s", "world")
    finally:
        request_id_var.reset(token)
    line = [ln for ln in capsys.readouterr().out.splitlines() if "hello world" in ln][-1]
    record = json.loads(line)
    assert record["request_id"] == "req-123" and record["msg"] == "hello world" and record["level"] == "INFO"
    configure_logging("text", "WARNING")
