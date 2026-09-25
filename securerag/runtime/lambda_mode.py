"""Running the API on AWS Lambda (container image + Lambda Web Adapter + Function URL).

Lambda mode is on when ``LAMBDA_MODE=1`` or when Lambda itself sets
``AWS_LAMBDA_FUNCTION_NAME``. At startup it:

* **Loads secrets from SSM Parameter Store** (SecureString, decrypted with the AWS-managed
  ``aws/ssm`` key). The parameter *names* come from environment variables and the values never
  live in the function's environment configuration. Any missing parameter is a hard startup error.
      SSM_AES_KEY_PARAM     -> SECURERAG_AES_KEY_B64                (required)
      SSM_API_KEYS_PARAM    -> hashed API keys JSON, written to /tmp (required)
      SSM_GROQ_KEY_PARAM    -> GROQ_API_KEY                          (optional: omit to run the mock LLM)
* **Moves every writable path under /tmp**, the only writable filesystem on Lambda. The encrypted
  demo index baked into the image at ``DEMO_INDEX_DIR`` (ciphertext, vectors, hashed terms, no
  plaintext) is copied to ``/tmp/securerag/index`` on cold start, because Chroma writes to its
  directory even when it only reads.
* **Refuses dev auth** (``ENV=dev`` or ``AUTH_MODE=dev``).
* **Sends audit entries to stdout** as JSON, so they land in CloudWatch Logs. The hash chain in
  ``/tmp`` exists per container instance and disappears with it (see SECURITY.md).

Admin ingestion is disabled in Lambda mode, and rate limiting is per container.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TMP_ROOT_ENV = "LAMBDA_TMP_ROOT"  # overridable for tests
REQUIRED_PARAMS = {"SSM_AES_KEY_PARAM": "aes_key", "SSM_API_KEYS_PARAM": "api_keys"}
OPTIONAL_PARAMS = {"SSM_GROQ_KEY_PARAM": "groq_key"}


class LambdaConfigError(RuntimeError):
    """Lambda mode is misconfigured; the function must not serve traffic."""


def is_lambda() -> bool:
    return os.getenv("LAMBDA_MODE", "").lower() in ("1", "true", "yes") or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def _tmp_root() -> Path:
    return Path(os.getenv(TMP_ROOT_ENV, "/tmp")) / "securerag"  # noqa: S108 - Lambda's only writable dir


def load_ssm_secrets(ssm_client: Any = None) -> dict[str, str]:
    """Fetch the configured SSM parameters; raise LambdaConfigError if any required one is missing."""
    wanted = {env: key for env, key in {**REQUIRED_PARAMS, **OPTIONAL_PARAMS}.items() if os.getenv(env)}
    missing_names = [env for env in REQUIRED_PARAMS if not os.getenv(env)]
    if missing_names:
        raise LambdaConfigError(f"missing environment variables naming SSM parameters: {missing_names}")
    if ssm_client is None:
        import boto3

        ssm_client = boto3.client("ssm")
    names = [os.environ[env] for env in wanted]
    resp = ssm_client.get_parameters(Names=names, WithDecryption=True)
    values = {p["Name"]: p["Value"] for p in resp.get("Parameters", [])}
    absent = [n for n in resp.get("InvalidParameters", [])] + [n for n in names if n not in values]
    required_absent = sorted({os.environ[env] for env in REQUIRED_PARAMS} & set(absent))
    if required_absent:
        raise LambdaConfigError(f"required SSM parameters not found: {required_absent}")
    return {key: values[os.environ[env]] for env, key in wanted.items() if os.environ[env] in values}


def prepare_lambda_environment(settings: Any, ssm_client: Any = None) -> Any:
    """Apply Lambda mode to ``settings`` and the process environment; returns the new settings."""
    if settings.env == "dev" or settings.auth_mode == "dev":
        raise LambdaConfigError("ENV=dev / AUTH_MODE=dev must never be deployed")

    secrets = load_ssm_secrets(ssm_client)
    os.environ["SECURERAG_AES_KEY_B64"] = secrets["aes_key"]
    if "groq_key" in secrets:
        os.environ["GROQ_API_KEY"] = secrets["groq_key"]

    root = _tmp_root()
    root.mkdir(parents=True, exist_ok=True)
    keys_file = root / "api_keys.json"
    keys_file.write_text(secrets["api_keys"], encoding="utf-8")
    keys_file.chmod(0o600)

    index = root / "index"
    baked = Path(os.getenv("DEMO_INDEX_DIR", "/opt/demo/index"))
    if not (index / "chroma").exists():
        if not baked.is_dir():
            raise LambdaConfigError(f"no demo index baked into the image at {baked}")
        shutil.copytree(baked, index, dirs_exist_ok=True)
        logger.info("copied demo index %s -> %s", baked, index)

    os.environ["AUDIT_STDOUT"] = "1"
    return settings.model_copy(update={
        "chroma_dir": str(index / "chroma"),
        "state_db_path": str(index / "ingest_state.sqlite"),
        "sparse_dir": str(index / "sparse"),
        "audit_log_path": str(root / "audit" / "audit.jsonl"),
        "api_keys_file": str(keys_file),
        "vector_backend": "chroma",
    })
