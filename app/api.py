from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from securerag.config import Settings, get_settings
from securerag.retrieval.store import build_engine, open_serving_stack
from securerag.security.audit import read_recent_audit_events
from securerag.security.encryption import VectorStoreEncryptor, DecryptionError
from securerag.security.rbac import ROLE_POLICY, AccessControlError
from securerag.pipeline.graph import SecureRAG

# Global application state holder
state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    injected = "engine" in state
    if not injected:
        settings = get_settings()
        encryptor = VectorStoreEncryptor()
        # Fails fast (no silent rebuild) on an empty store, wrong key or model mismatch.
        stack = open_serving_stack(settings, encryptor)
        engine = build_engine(settings, encryptor, stack)

        state["settings"] = settings
        state["encryptor"] = encryptor
        state["store"] = stack.store
        state["retriever"] = stack.retriever
        state["engine"] = engine
    yield
    if not injected:
        state.clear()


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="SecureRAG API",
    version="1.0.0",
    description="Production-style Security-Hardened Enterprise RAG System",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
dist_dir = frontend_dir / "dist"
assets_dir = dist_dir / "assets"
src_dir = frontend_dir / "src"

if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")
elif src_dir.exists():
    app.mount("/src", StaticFiles(directory=src_dir), name="frontend-src")


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    role: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)


@app.get("/api/health")
def health():
    store = state.get("store")
    chunk_count = store.count() if store else 0
    return {
        "status": "ok",
        "chunks": chunk_count,
        "roles": list(ROLE_POLICY.keys()),
    }


@app.get("/api/roles")
def get_roles():
    return ROLE_POLICY


@app.get("/api/audit")
def get_audit_trail(limit: int = Query(default=20, ge=1, le=100)):
    settings: Settings = state.get("settings") or get_settings()
    events = read_recent_audit_events(settings.audit_log_path, limit=limit)
    return events


@app.post("/api/query")
def query_endpoint(request: QueryRequest):
    engine: SecureRAG = state.get("engine")
    if not engine:
        raise HTTPException(status_code=503, detail="SecureRAG engine is not initialized.")

    try:
        result = engine.query(request.query, request.role, request.top_k)
    except AccessControlError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DecryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}") from exc

    return {
        "request_id": result["request_id"],
        "answer": result["answer"],
        "role": result["user_role"],
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
