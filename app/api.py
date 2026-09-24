"""SecureRAG HTTP API.

Security posture:
* The caller's role comes from the authenticated principal (API key, JWT, or the dev-only
  header). The request body cannot carry a role: unknown fields are rejected with 422.
* CORS is limited to ``CORS_ORIGINS``; there are no credentialed wildcard origins.
* Errors return a generic message and the ``request_id``; details go to the server log only.
* Request bodies over ``MAX_REQUEST_BYTES`` are rejected (413); queries are rate limited per
  principal (``RATE_LIMIT``, e.g. "30/minute") with 429.
"""
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from securerag.config import Settings, get_settings
from securerag.pipeline.graph import SecureRAG
from securerag.retrieval.store import build_engine, open_serving_stack
from securerag.security.audit import read_recent_audit_events
from securerag.security.auth import Authenticator, AuthError, Principal
from securerag.security.encryption import VectorStoreEncryptor
from securerag.security.rate_limit import RateLimiter
from securerag.security.rbac import ROLE_POLICY, AccessControlError

logger = logging.getLogger("securerag.api")

# Global application state holder (tests inject components here before startup).
state: dict[str, Any] = {}


def _settings() -> Settings:
    return state.get("settings") or get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    injected = set(state)
    settings = _settings()
    state.setdefault("settings", settings)
    if "authenticator" not in state:
        # Misconfigured auth (e.g. AUTH_MODE=dev outside ENV=dev, no keys) stops startup here.
        state["authenticator"] = Authenticator(settings)
    if "rate_limiter" not in state:
        state["rate_limiter"] = RateLimiter(settings.rate_limit, settings.rate_limit_storage)
    if "engine" not in state:
        encryptor = VectorStoreEncryptor()
        # Fails fast (no silent rebuild) on an empty store, wrong key or model mismatch.
        stack = open_serving_stack(settings, encryptor)
        state["encryptor"] = encryptor
        state["store"] = stack.store
        state["retriever"] = stack.retriever
        state["engine"] = build_engine(settings, encryptor, stack)
    yield
    for key in set(state) - injected:
        state.pop(key, None)


app = FastAPI(
    title="SecureRAG API",
    version="1.1.0",
    description="Security-hardened enterprise RAG system",
    lifespan=lifespan,
)

_boot_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_boot_settings.cors_origin_list,
    allow_credentials=False,  # auth travels in headers, never cookies
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Dev-Role", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Assigns a request id, enforces the body size limit and hides internal errors."""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    if not (len(request_id) <= 64 and request_id.replace("-", "").isalnum()):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    if request.method in ("POST", "PUT", "PATCH"):
        length = request.headers.get("content-length")
        limit = _settings().max_request_bytes
        if length is None:
            return JSONResponse({"detail": "Content-Length required", "request_id": request_id}, status_code=411)
        if not length.isdigit() or int(length) > limit:
            return JSONResponse({"detail": f"Request body exceeds {limit} bytes", "request_id": request_id},
                                status_code=413)
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001 - last-resort handler; details stay in the server log
        logger.exception("unhandled error request_id=%s path=%s", request_id, request.url.path)
        response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
    response.headers["X-Request-ID"] = request_id
    return response


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
dist_dir = frontend_dir / "dist"
assets_dir = dist_dir / "assets"
src_dir = frontend_dir / "src"

if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")
elif src_dir.exists():
    app.mount("/src", StaticFiles(directory=src_dir), name="frontend-src")


class QueryRequest(BaseModel):
    # extra="forbid": a client-supplied "role" (or anything else unexpected) is rejected with 422.
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=10)


# ------------------------------------------------------------------------- dependencies

def get_principal(request: Request) -> Principal:
    authenticator: Authenticator | None = state.get("authenticator")
    if authenticator is None:
        raise HTTPException(status_code=503, detail="Service is starting")
    try:
        principal = authenticator.authenticate(request.headers)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    request.state.principal = principal
    return principal


def rate_limited(principal: Principal = Depends(get_principal)) -> Principal:
    limiter: RateLimiter | None = state.get("rate_limiter")
    if limiter is not None and not limiter.hit(principal.id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded", headers={"Retry-After": "60"})
    return principal


# ------------------------------------------------------------------------- endpoints

@app.get("/api/health")
def health():
    """Liveness: the process is up."""
    store = state.get("store")
    return {"status": "ok", "chunks": store.count() if store else 0, "roles": list(ROLE_POLICY.keys())}


@app.get("/api/roles")
def get_roles():
    return ROLE_POLICY


@app.get("/api/auth/mode")
def auth_mode():
    """Lets the UI decide which credential input to show."""
    settings = _settings()
    return {"mode": settings.auth_mode, "env": settings.env}


@app.get("/api/auth/me")
def whoami(principal: Principal = Depends(get_principal)):
    return {"principal": principal.id, "role": principal.role, "method": principal.method,
            "policy": ROLE_POLICY[principal.role]}


@app.get("/api/audit")
def get_audit_trail(limit: int = Query(default=20, ge=1, le=100), principal: Principal = Depends(rate_limited)):
    settings = _settings()
    if principal.role not in settings.audit_reader_role_set:
        raise HTTPException(status_code=403, detail="Your role may not read the audit log")
    return read_recent_audit_events(settings.audit_log_path, limit=limit)


@app.post("/api/query")
def query_endpoint(body: QueryRequest, request: Request, principal: Principal = Depends(rate_limited)):
    engine: SecureRAG | None = state.get("engine")
    if not engine:
        raise HTTPException(status_code=503, detail="SecureRAG engine is not initialized.")

    request_id = request.state.request_id
    try:
        result = engine.query(body.query, principal.role, body.top_k, principal_id=principal.id,
                              request_id=request_id)
    except AccessControlError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except ValueError as exc:
        # Input validation errors raised by the pipeline's validate node (e.g. empty query).
        logger.info("rejected query request_id=%s: %s", request_id, exc)
        raise HTTPException(status_code=400, detail="Invalid query") from exc

    return {
        "request_id": result["request_id"],
        "answer": result["answer"],
        "role": result["user_role"],
        "principal": principal.id,
        "retrieved": len(result.get("retrieved", [])),
        "authorized": len(result.get("authorized", [])),
        "blocked": result.get("blocked_count", 0),
        "flagged_chunks": result.get("flagged_count", 0),
        "reranked": result.get("reranked", False),
        "dropped_for_budget": result.get("dropped_for_budget", 0),
        "latency_ms": result.get("latency_ms", 0.0),
        "sources": [h.metadata.get("source") for h in result.get("authorized", [])],
        "context_excerpts": result.get("context_excerpts", []),
    }


@app.get("/", include_in_schema=False)
def root():
    dist_index = dist_dir / "index.html"
    index_path = dist_index if dist_index.exists() else (frontend_dir / "index.html")
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(index_path)
