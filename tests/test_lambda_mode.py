"""Lambda mode: SSM secrets (moto), /tmp relocation, dev-auth refusal, no CORS middleware,
admin endpoints disabled, and a real cold start from a baked encrypted index. No real AWS."""
import base64
import importlib
import json
import os
from pathlib import Path

import pytest

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

from securerag.config import Settings, get_settings  # noqa: E402
from securerag.runtime import lambda_mode  # noqa: E402
from securerag.security.auth import hash_api_key  # noqa: E402

REGION = "ap-south-1"
AES = base64.b64encode(os.urandom(32)).decode()
KEYS_JSON = json.dumps({"keys": [
    {"id": "g", "hash": hash_api_key("guest-demo-key"), "principal": "demo-guest", "role": "guest"},
    {"id": "e", "hash": hash_api_key("exec-demo-key"), "principal": "demo-exec", "role": "exec"},
]})


@pytest.fixture
def aws_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(var, "testing")
    monkeypatch.setenv("SSM_AES_KEY_PARAM", "/securerag/aes_key_b64")
    monkeypatch.setenv("SSM_API_KEYS_PARAM", "/securerag/api_keys_json")
    monkeypatch.setenv("SSM_GROQ_KEY_PARAM", "/securerag/groq_api_key")
    monkeypatch.setenv(lambda_mode.TMP_ROOT_ENV, str(tmp_path / "tmp"))
    # prepare_lambda_environment() writes these into os.environ; register them so they're restored.
    for var in ("AUDIT_STDOUT", "SECURERAG_AES_KEY_B64", "GROQ_API_KEY"):
        monkeypatch.setenv(var, "placeholder")
        monkeypatch.delenv(var)
    with moto.mock_aws():
        ssm = boto3.client("ssm", region_name=REGION)
        yield ssm


def _put(ssm, aes=True, keys=True, groq=True):
    if aes:
        ssm.put_parameter(Name="/securerag/aes_key_b64", Value=AES, Type="SecureString")
    if keys:
        ssm.put_parameter(Name="/securerag/api_keys_json", Value=KEYS_JSON, Type="SecureString")
    if groq:
        ssm.put_parameter(Name="/securerag/groq_api_key", Value="gsk_test", Type="SecureString")


def test_is_lambda_detection(monkeypatch):
    monkeypatch.delenv("LAMBDA_MODE", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert not lambda_mode.is_lambda()
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "securerag-api")
    assert lambda_mode.is_lambda()


def test_load_ssm_secrets(aws_env):
    _put(aws_env)
    secrets = lambda_mode.load_ssm_secrets(aws_env)
    assert secrets == {"aes_key": AES, "api_keys": KEYS_JSON, "groq_key": "gsk_test"}


def test_optional_groq_key_may_be_absent(aws_env):
    _put(aws_env, groq=False)
    assert "groq_key" not in lambda_mode.load_ssm_secrets(aws_env)


@pytest.mark.parametrize("missing", ["aes", "keys"])
def test_missing_required_secret_fails_fast(aws_env, missing):
    _put(aws_env, aes=missing != "aes", keys=missing != "keys")
    with pytest.raises(lambda_mode.LambdaConfigError, match="required SSM parameters not found"):
        lambda_mode.load_ssm_secrets(aws_env)


def test_missing_parameter_name_env_fails(aws_env, monkeypatch):
    monkeypatch.delenv("SSM_AES_KEY_PARAM")
    with pytest.raises(lambda_mode.LambdaConfigError, match="SSM_AES_KEY_PARAM"):
        lambda_mode.load_ssm_secrets(aws_env)


def _baked_index(tmp_path) -> Path:
    baked = tmp_path / "baked"
    (baked / "chroma").mkdir(parents=True)
    (baked / "chroma" / "marker").write_text("x")
    (baked / "sparse").mkdir()
    return baked


def test_prepare_relocates_everything_to_tmp(aws_env, tmp_path, monkeypatch):
    _put(aws_env)
    monkeypatch.setenv("DEMO_INDEX_DIR", str(_baked_index(tmp_path)))
    settings = lambda_mode.prepare_lambda_environment(Settings(env="prod", auth_mode="api_key"), aws_env)
    root = tmp_path / "tmp" / "securerag"
    for path in (settings.chroma_dir, settings.state_db_path, settings.sparse_dir, settings.audit_log_path,
                 settings.api_keys_file):
        assert Path(path).resolve().is_relative_to(root.resolve()), path
    assert (root / "index" / "chroma" / "marker").exists()
    assert json.loads(Path(settings.api_keys_file).read_text()) == json.loads(KEYS_JSON)
    assert os.environ["SECURERAG_AES_KEY_B64"] == AES and os.environ["GROQ_API_KEY"] == "gsk_test"
    assert os.environ["AUDIT_STDOUT"] == "1"


@pytest.mark.parametrize("env,auth", [("dev", "api_key"), ("prod", "dev")])
def test_dev_auth_is_never_deployed(aws_env, env, auth):
    _put(aws_env)
    with pytest.raises(lambda_mode.LambdaConfigError, match="never be deployed"):
        lambda_mode.prepare_lambda_environment(Settings(env=env, auth_mode=auth), aws_env)


def test_missing_baked_index_fails(aws_env, tmp_path, monkeypatch):
    _put(aws_env)
    monkeypatch.setenv("DEMO_INDEX_DIR", str(tmp_path / "nope"))
    with pytest.raises(lambda_mode.LambdaConfigError, match="no demo index"):
        lambda_mode.prepare_lambda_environment(Settings(env="prod", auth_mode="api_key"), aws_env)


# ------------------------------------------------------------------------------ API module in Lambda mode

@pytest.fixture
def lambda_api(monkeypatch):
    """app.api re-imported with LAMBDA_MODE=1; restored afterwards."""
    monkeypatch.setenv("LAMBDA_MODE", "1")
    import app.api as api

    # reload() rebinds module globals; keep the one `state` dict other test modules imported.
    shared_state = api.state
    api = importlib.reload(api)
    api.state = shared_state
    yield api
    api.state.clear()
    monkeypatch.delenv("LAMBDA_MODE")
    importlib.reload(api)
    api.state = shared_state


def test_no_cors_middleware_in_lambda_mode(lambda_api):
    from fastapi.middleware.cors import CORSMiddleware

    assert lambda_api.LAMBDA
    assert not any(m.cls is CORSMiddleware for m in lambda_api.app.user_middleware)


def test_cors_middleware_present_outside_lambda():
    from fastapi.middleware.cors import CORSMiddleware

    import app.api as api

    assert any(m.cls is CORSMiddleware for m in api.app.user_middleware)


def test_cold_start_from_baked_index_serves_with_ssm_keys(aws_env, tmp_path, monkeypatch, lambda_api, capsys):
    """Real model + real persistent Chroma: bake an index, cold-start the API in Lambda mode from it."""
    from fastapi.testclient import TestClient

    from securerag.retrieval.store import run_ingestion
    from securerag.security.encryption import VectorStoreEncryptor

    baked = tmp_path / "baked"
    sample = Path(__file__).resolve().parent.parent / "data" / "sample"
    build_settings = Settings(data_dir=str(sample), chroma_dir=str(baked / "chroma"),
                              state_db_path=str(baked / "ingest_state.sqlite"), sparse_dir=str(baked / "sparse"))
    run_ingestion(build_settings, VectorStoreEncryptor(key_b64=AES))
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()  # the image build and the Lambda are different processes

    _put(aws_env)
    monkeypatch.setenv("DEMO_INDEX_DIR", str(baked))
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    lambda_api.state.clear()
    try:
        with TestClient(lambda_api.app, raise_server_exceptions=False) as client:
            assert client.get("/api/ready").status_code == 200
            guest = client.post("/api/query", json={"query": "What was the Q3 gross margin?"},
                                headers={"X-API-Key": "guest-demo-key"}).json()
            exec_ = client.post("/api/query", json={"query": "What was the Q3 gross margin?"},
                                headers={"X-API-Key": "exec-demo-key"}).json()
            admin = client.post("/api/admin/ingest", json={}, headers={"X-API-Key": "exec-demo-key"})
            assert client.post("/api/query", json={"query": "x"}).status_code == 401
    finally:
        get_settings.cache_clear()
    assert guest["role"] == "guest" and "34.2" not in guest["answer"]
    assert exec_["role"] == "exec" and "34.2" in exec_["answer"]
    assert admin.status_code in (403, 404)
    audit_lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith('{"audit"')]
    assert audit_lines and json.loads(audit_lines[-1])["audit"]["principal_id"] == "demo-exec"
    # Nothing was written next to the baked index: everything went to /tmp.
    assert not (baked / "audit").exists()


def test_admin_endpoints_disabled_in_lambda_mode(lambda_api, rag_stack):
    from fastapi.testclient import TestClient

    from securerag.security.auth import Authenticator

    settings = rag_stack["settings"].model_copy(update={"env": "dev", "auth_mode": "dev"})
    lambda_api.state.update({"settings": settings, "authenticator": Authenticator(settings),
                             "store": rag_stack["stack"].store, "engine": rag_stack["engine"],
                             "encryptor": rag_stack["encryptor"]})
    with TestClient(lambda_api.app) as client:
        assert client.post("/api/admin/ingest", json={}, headers={"X-Dev-Role": "admin"}).status_code == 404
        assert client.get("/api/admin/ingest/abcdefabcdef", headers={"X-Dev-Role": "admin"}).status_code == 404

