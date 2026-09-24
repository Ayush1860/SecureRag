import os
import time
import uuid
from typing import Any, TypedDict
from langgraph.graph import StateGraph, START, END

from securerag.llm.providers import SYSTEM_PROMPT, call_llm
from securerag.retrieval.hybrid import HybridRetriever, Chunk
from securerag.retrieval.vector_store import VectorStore
from securerag.security.audit import audit_event
from securerag.security.rbac import authorize, build_chroma_filter, validate_role
from securerag.security.sanitizer import build_safe_context_block, sanitize_chunk


class RAGState(TypedDict, total=False):
    query: str
    user_role: str
    request_id: str
    top_k: int
    retrieved: list[Chunk]
    authorized: list[Chunk]
    blocked_count: int
    flagged_count: int
    context_block: str
    context_excerpts: list[dict[str, Any]]
    answer: str
    latency_ms: float
    _start: float


class SecureRAG:
    """
    Security-hardened RAG pipeline orchestrated via LangGraph.
    
    Nodes enforce strict security perimeters:
    1. validate (RBAC identity verification & request framing)
    2. retrieve (ChromaDB dense + BM25 sparse hybrid retrieval with metadata filter)
    3. authorize (Post-retrieval verification to guard against cross-boundary leaks)
    4. decrypt_sanitize (AES-256-GCM payload decryption & prompt injection quarantine)
    5. generate (Grounded response generation with instruction isolation)
    6. audit (Immutable JSONL audit trail recording cryptographic hashes)
    """

    def __init__(self, store: VectorStore, retriever: HybridRetriever, encryptor, audit_path: str):
        self.store = store
        self.retriever = retriever
        self.encryptor = encryptor
        self.audit_path = audit_path
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(RAGState)
        graph.add_node("validate", self.validate_node)
        graph.add_node("retrieve", self.retrieve_node)
        graph.add_node("authorize", self.authorize_node)
        graph.add_node("decrypt_sanitize", self.decrypt_sanitize_node)
        graph.add_node("generate", self.generate_node)
        graph.add_node("audit", self.audit_node)

        graph.add_edge(START, "validate")
        graph.add_edge("validate", "retrieve")
        graph.add_edge("retrieve", "authorize")
        graph.add_edge("authorize", "decrypt_sanitize")
        graph.add_edge("decrypt_sanitize", "generate")
        graph.add_edge("generate", "audit")
        graph.add_edge("audit", END)

        return graph.compile()

    def validate_node(self, state: RAGState) -> RAGState:
        role = state.get("user_role", "")
        validate_role(role)
        query = state.get("query", "").strip()
        if not query:
            raise ValueError("Query cannot be empty")

        state["request_id"] = state.get("request_id") or str(uuid.uuid4())
        state["_start"] = time.perf_counter()
        return state

    def retrieve_node(self, state: RAGState) -> RAGState:
        role_filter = build_chroma_filter(state["user_role"])
        top_k = int(state.get("top_k", 5))
        state["retrieved"] = self.retriever.retrieve(
            query=state["query"],
            top_k=top_k,
            where=role_filter,
        )
        return state

    def authorize_node(self, state: RAGState) -> RAGState:
        retrieved = state.get("retrieved", [])
        role = state["user_role"]
        allowed = [h for h in retrieved if authorize(role, h.metadata)]
        state["authorized"] = allowed
        state["blocked_count"] = len(retrieved) - len(allowed)
        return state

    def decrypt_sanitize_node(self, state: RAGState) -> RAGState:
        authorized = state.get("authorized", [])
        if not authorized:
            state["context_block"] = "No authorized document excerpts available for this query and clearance level."
            state["context_excerpts"] = []
            state["flagged_count"] = 0
            return state

        plaintexts = []
        excerpts = []
        flagged_total = 0

        # Payloads are fetched and decrypted only here, only for this request's authorized top-k.
        payloads = {cid: ct for cid, ct, _ in self.store.get([h.id for h in authorized])}
        for h in authorized:
            if h.id not in payloads:
                continue  # deleted between retrieval and fetch
            pt = self.encryptor.decrypt(payloads[h.id])
            plaintexts.append(pt)
            _, is_flagged = sanitize_chunk(pt)
            if is_flagged:
                flagged_total += 1
            excerpts.append({
                "source": h.metadata.get("source", "unknown"),
                "department": h.metadata.get("department", "general"),
                "clearance": h.metadata.get("clearance", "public"),
                "text": pt,
                "flagged": is_flagged,
            })

        context_block, _ = build_safe_context_block(plaintexts)
        state["context_block"] = context_block
        state["context_excerpts"] = excerpts
        state["flagged_count"] = flagged_total
        return state

    def generate_node(self, state: RAGState) -> RAGState:
        if not state.get("authorized"):
            state["answer"] = (
                "I do not have access to any authorized documents to answer this question "
                "under your current role and clearance level."
            )
            return state

        user_prompt = f"{state['context_block']}\n\nQuestion: {state['query']}"
        state["answer"] = call_llm(SYSTEM_PROMPT, user_prompt)
        return state

    def audit_node(self, state: RAGState) -> RAGState:
        start = state.get("_start", time.perf_counter())
        state["latency_ms"] = (time.perf_counter() - start) * 1000
        provider = os.getenv("LLM_PROVIDER", "mock")

        audit_event(
            self.audit_path,
            request_id=state["request_id"],
            user_role=state["user_role"],
            query=state["query"],
            retrieved_ids=[h.id for h in state.get("retrieved", [])],
            authorized_count=len(state.get("authorized", [])),
            blocked_count=state.get("blocked_count", 0),
            flagged_count=state.get("flagged_count", 0),
            answer=state.get("answer", ""),
            provider=provider,
            latency_ms=state["latency_ms"],
        )
        return state

    def query(self, query: str, user_role: str, top_k: int = 5) -> dict[str, Any]:
        return self.graph.invoke({"query": query, "user_role": user_role, "top_k": top_k})
