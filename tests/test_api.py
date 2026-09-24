import json
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api import app, state
from securerag.security.auth import AuthConfigError, Authenticator, hash_api_key
from securerag.security.rate_limit import RateLimiter

JWT_SECRET = "s" * 40


def _client(rag_stack, *, settings=None, **extra_state):
    settings = settings or rag_stack["settings"].model_copy(update={"env": "dev", "auth_mode": "dev"})
    state.clear()
    state.update({
        "settings": settings,
        "authenticator": Authenticator(settings),
        "store": rag_stack["stack"].store,
        "engine": rag_stack["engine"],
        "encryptor": rag_stack["encryptor"],
        **extra_state,
    })
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_with_mock_state(rag_stack):
    with _client(rag_stack) as client:
        yield client
    state.clear()


def _dev(role):
    return {"X-Dev-Role": role}


# --------------------------------------------------------------------------- basics (dev auth)

def test_health_endpoint(client_with_mock_state):
    res = client_with_mock_state.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "guest" in data["roles"]
    assert res.headers["X-Request-ID"]


def test_roles_endpoint(client_with_mock_state):
    res = client_with_mock_state.get("/api/roles")
    assert res.status_code == 200
    assert "finance_lead" in res.json()


def test_query_endpoint_success(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "Where is company located?", "top_k": 2},
                                      headers=_dev("employee"))
    assert res.status_code == 200
    data = res.json()
    assert "request_id" in data
    assert data["role"] == "employee"
    assert data["principal"] == "dev:employee"
    assert "answer" in data
    assert "context_excerpts" in data


def test_query_endpoint_invalid_role(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "hello", "top_k": 2}, headers=_dev("hacker"))
    assert res.status_code == 401


def test_query_endpoint_empty_query(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "", "top_k": 2}, headers=_dev("guest"))
    assert res.status_code == 422  # pydantic min_length=1 validation error


def test_root_serves_html(client_with_mock_state):
    res = client_with_mock_state.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "SecureRAG" in res.text


def test_audit_endpoint(client_with_mock_state):
    client_with_mock_state.post("/api/query", json={"query": "test audit query", "top_k": 2},
                                headers=_dev("employee"))
    res = client_with_mock_state.get("/api/audit?limit=5", headers=_dev("exec"))
    assert res.status_code == 200
    logs = res.json()
    assert isinstance(logs, list) and len(logs) >= 1
    assert "query_hash" in logs[0]
    assert logs[0]["principal_id"] == "dev:employee"
    assert {"seq", "prev_hash", "entry_hash"} <= set(logs[0])


def test_audit_requires_privileged_role(client_with_mock_state):
    assert client_with_mock_state.get("/api/audit", headers=_dev("employee")).status_code == 403
    assert client_with_mock_state.get("/api/audit", headers=_dev("admin")).status_code == 200


def test_admin_role_sees_no_documents(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "revenue margin onboarding"},
                                      headers=_dev("admin"))
    assert res.status_code == 200
    assert res.json()["authorized"] == 0


# --------------------------------------------------------------------------- role spoofing

def test_role_in_body_is_rejected(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", json={"query": "Q3 gross margin", "role": "exec"},
                                      headers=_dev("guest"))
    assert res.status_code == 422


def test_role_cannot_be_spoofed_with_api_keys(rag_stack, tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps({"keys": [
        {"id": "g1", "hash": hash_api_key("guest-key"), "principal": "gina", "role": "guest"},
    ]}), encoding="utf-8")
    settings = rag_stack["settings"].model_copy(update={"auth_mode": "api_key", "api_keys_file": str(keys_file)})
    with _client(rag_stack, settings=settings) as client:
        ok = client.post("/api/query", json={"query": "What is the Q3 gross margin?"},
                         headers={"X-API-Key": "guest-key", "X-Dev-Role": "exec"})
        assert ok.status_code == 200
        body = ok.json()
        assert body["role"] == "guest" and body["principal"] == "gina"
        assert "34.2" not in body["answer"]
        assert client.post("/api/query", json={"query": "x", "role": "exec"},
                           headers={"X-API-Key": "guest-key"}).status_code == 422
        assert client.post("/api/query", json={"query": "x"}, headers={"X-API-Key": "nope"}).status_code == 401
        assert client.post("/api/query", json={"query": "x"}).status_code == 401
    state.clear()


def test_dev_mode_refused_outside_dev_env(rag_stack):
    settings = rag_stack["settings"].model_copy(update={"env": "prod", "auth_mode": "dev"})
    with pytest.raises(AuthConfigError):
        Authenticator(settings)


def test_api_key_mode_requires_keys_file(rag_stack, tmp_path):
    settings = rag_stack["settings"].model_copy(update={"auth_mode": "api_key",
                                                        "api_keys_file": str(tmp_path / "missing.json")})
    with pytest.raises(AuthConfigError):
        Authenticator(settings)


# --------------------------------------------------------------------------- JWT

@pytest.fixture
def jwt_client(rag_stack):
    settings = rag_stack["settings"].model_copy(update={"auth_mode": "jwt", "jwt_secret": JWT_SECRET,
                                                        "jwt_audience": "securerag", "jwt_leeway_seconds": 0})
    with _client(rag_stack, settings=settings) as client:
        yield client
    state.clear()


def _token(secret=JWT_SECRET, alg="HS256", **claims):
    base = {"sub": "fin-1", "role": "finance_lead", "aud": "securerag", "exp": int(time.time()) + 300}
    base.update(claims)
    return jwt.encode({k: v for k, v in base.items() if v is not None}, secret, algorithm=alg)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_jwt_valid_token_sets_role(jwt_client):
    res = jwt_client.post("/api/query", json={"query": "What is the Q3 gross margin?"}, headers=_bearer(_token()))
    assert res.status_code == 200
    assert res.json()["role"] == "finance_lead" and res.json()["principal"] == "fin-1"
    assert "34.2%" in res.json()["answer"]


@pytest.mark.parametrize("token", [
    pytest.param(lambda: _token(exp=int(time.time()) - 5), id="expired"),
    pytest.param(lambda: _token(secret="x" * 40), id="forged-signature"),
    pytest.param(lambda: _token(aud="someone-else"), id="wrong-audience"),
    pytest.param(lambda: _token(role="superuser"), id="unknown-role"),
    pytest.param(lambda: _token(role=None), id="no-role-claim"),
    pytest.param(lambda: _token(exp=None), id="no-exp"),
    pytest.param(lambda: jwt.encode({"sub": "x", "role": "exec", "aud": "securerag",
                                     "exp": int(time.time()) + 60}, None, algorithm="none"), id="alg-none"),
    pytest.param(lambda: "not.a.jwt", id="garbage"),
])
def test_jwt_rejects_bad_tokens(jwt_client, token):
    res = jwt_client.post("/api/query", json={"query": "What is the Q3 gross margin?"}, headers=_bearer(token()))
    assert res.status_code == 401


def test_jwt_secret_must_be_long(rag_stack):
    settings = rag_stack["settings"].model_copy(update={"auth_mode": "jwt", "jwt_secret": "short"})
    with pytest.raises(AuthConfigError):
        Authenticator(settings)


# --------------------------------------------------------------------------- API hardening

def test_internal_errors_are_generic(rag_stack):
    class Boom:
        def query(self, *a, **kw):
            raise RuntimeError("stack trace with secret details")

    with _client(rag_stack, engine=Boom()) as client:
        res = client.post("/api/query", json={"query": "hi"}, headers=_dev("guest"))
    state.clear()
    assert res.status_code == 500
    body = res.json()
    assert body["detail"] == "Internal server error" and body["request_id"]
    assert "secret" not in res.text


def test_oversized_body_rejected(client_with_mock_state):
    res = client_with_mock_state.post("/api/query", content=json.dumps({"query": "x" * 20_000}),
                                      headers={**_dev("guest"), "Content-Type": "application/json"})
    assert res.status_code == 413


def test_rate_limit_per_principal(rag_stack):
    with _client(rag_stack, rate_limiter=RateLimiter("2/minute")) as client:
        codes = [client.post("/api/query", json={"query": "revenue"}, headers=_dev("guest")).status_code
                 for _ in range(3)]
        other = client.post("/api/query", json={"query": "revenue"}, headers=_dev("employee")).status_code
    state.clear()
    assert codes == [200, 200, 429]
    assert other == 200  # limits are per principal


def test_cors_does_not_allow_arbitrary_origins(client_with_mock_state):
    res = client_with_mock_state.options("/api/query", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert res.headers.get("access-control-allow-origin") != "*"
    assert res.headers.get("access-control-allow-origin") != "https://evil.example"


def test_auth_mode_and_whoami(client_with_mock_state):
    assert client_with_mock_state.get("/api/auth/mode").json() == {"mode": "dev", "env": "dev"}
    me = client_with_mock_state.get("/api/auth/me", headers=_dev("finance_lead")).json()
    assert me["role"] == "finance_lead" and me["method"] == "dev"
