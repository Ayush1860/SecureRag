import math
import os
import time
import uuid
from typing import Any, Callable, TypedDict
from langgraph.graph import StateGraph, START, END

from securerag.llm.providers import SYSTEM_PROMPT, call_llm
from securerag.retrieval.hybrid import HybridRetriever, Chunk
from securerag.retrieval.rerank import Reranker
from securerag.retrieval.vector_store import VectorStore
from securerag.security.audit import audit_event
from securerag.security.encryption import aad_for
from securerag.security.rbac import authorize, build_chroma_filter, validate_role
from securerag.security.sanitizer import build_safe_context_block, sanitize_chunk


class RAGState(TypedDict, total=False):
    query: str
    user_role: str
    principal_id: str
    request_id: str
    top_k: int
    retrieved: list[Chunk]
    authorized: list[Chunk]
    reranked: bool
    dropped_for_budget: int
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
    2. retrieve (dense + partitioned sparse hybrid retrieval, RBAC pre-filtered, RRF fused)
    3. rerank (optional cross-encoder over the fused top-N; only authorized candidates are decrypted)
    4. authorize (Post-retrieval verification to guard against cross-boundary leaks)
    5. decrypt_sanitize (fetch + decrypt authorized top-k by ID, context token budget, injection quarantine)
    6. generate (Grounded response generation with instruction isolation)
    7. audit (JSONL audit trail recording hashes of query and answer)
    """

    def __init__(
        self,
        store: VectorStore,
        retriever: HybridRetriever,
        encryptor,
        audit_path: str,
        *,
        reranker: Reranker | None = None,
        rerank_top_n: int = 30,
        context_token_budget: int = 3000,
        token_counter: Callable[[str], int] | None = None,
    ):
        self.store = store
        self.retriever = retriever
        self.encryptor = encryptor
        self.audit_path = audit_path
        self.reranker = reranker
        self.rerank_top_n = rerank_top_n
        self.context_token_budget = context_token_budget
        self.token_counter = token_counter or approx_tokens
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(RAGState)
        graph.add_node("validate", self.validate_node)
        graph.add_node("retrieve", self.retrieve_node)
        graph.add_node("rerank", self.rerank_node)
        graph.add_node("authorize", self.authorize_node)
        graph.add_node("decrypt_sanitize", self.decrypt_sanitize_node)
        graph.add_node("generate", self.generate_node)
        graph.add_node("audit", self.audit_node)

        graph.add_edge(START, "validate")
        graph.add_edge("validate", "retrieve")
        graph.add_conditional_edges("retrieve", lambda _s: "rerank" if self.reranker else "authorize",
                                    {"rerank": "rerank", "authorize": "authorize"})
        graph.add_edge("rerank", "authorize")
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
            limit=max(top_k, self.rerank_top_n) if self.reranker else None,
        )
        state["reranked"] = False
        return state

    def rerank_node(self, state: RAGState) -> RAGState:
        """Cross-encoder rerank of the fused candidates, then cut to top_k.

        Only candidates the caller is authorized for are decrypted and scored; anything else is
        passed through untouched (unscored, never decrypted) for the authorize node to block.
        Plaintext lives only inside this function.
        """
        role, top_k = state["user_role"], int(state.get("top_k", 5))
        candidates = state.get("retrieved", [])
        allowed = [h for h in candidates if authorize(role, h.metadata)]
        denied = [h for h in candidates if not authorize(role, h.metadata)]
        payloads = {cid: (ct, meta) for cid, ct, meta in self.store.get([h.id for h in allowed])}
        allowed = [h for h in allowed if h.id in payloads]
        texts = [self.encryptor.decrypt(payloads[h.id][0], aad=aad_for(h.id, payloads[h.id][1])) for h in allowed]
        scores = self.reranker.score(state["query"], texts)
        del texts
        for h, s in zip(allowed, scores):
            h.score = s
        ranked = sorted(allowed, key=lambda h: h.score, reverse=True)
        state["retrieved"] = ranked[:top_k] + denied
        state["reranked"] = True
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
        state["dropped_for_budget"] = 0
        if not authorized:
            state["context_block"] = "No authorized document excerpts available for this query and clearance level."
            state["context_excerpts"] = []
            state["flagged_count"] = 0
            return state

        # Context token budget: keep chunks in rank order until the budget is used up. The chunk's
        # token count is recorded at ingest, so dropped chunks are never fetched or decrypted.
        # The top-ranked chunk is always kept.
        kept, used = [], 0
        for h in authorized:
            cost = int(h.metadata.get("tokens") or 0)
            if kept and cost and used + cost > self.context_token_budget:
                continue
            kept.append(h)
            used += cost
        state["dropped_for_budget"] = len(authorized) - len(kept)
        authorized = kept

        plaintexts = []
        flags: list[bool] = []
        excerpts = []
        flagged_total = 0

        # Payloads are fetched and decrypted only here, only for this request's authorized top-k.
        payloads = {cid: (ct, meta) for cid, ct, meta in self.store.get([h.id for h in authorized])}
        for h in authorized:
            if h.id not in payloads:
                continue  # deleted between retrieval and fetch
            ciphertext, stored_meta = payloads[h.id]
            # AAD binds the payload to this chunk ID and its stored labels: a relabelled or
            # swapped payload fails authentication here instead of reaching the prompt.
            pt = self.encryptor.decrypt(ciphertext, aad=aad_for(h.id, stored_meta))
            if not h.metadata.get("tokens"):
                # Chunk from before token counts were stored: estimate after decryption.
                cost = self.token_counter(pt)
                if plaintexts and used + cost > self.context_token_budget:
                    state["dropped_for_budget"] += 1
                    continue
                used += cost
            plaintexts.append(pt)
            # Injection verdict computed once at ingest; re-scan only chunks that predate the flag.
            stored_flag = stored_meta.get("injection_flagged")
            is_flagged = bool(stored_flag) if stored_flag is not None else sanitize_chunk(pt)[1]
            flags.append(is_flagged)
            if is_flagged:
                flagged_total += 1
            excerpts.append({
                "source": h.metadata.get("source", "unknown"),
                "department": h.metadata.get("department", "general"),
                "clearance": h.metadata.get("clearance", "public"),
                "text": pt,
                "flagged": is_flagged,
            })

        context_block, _ = build_safe_context_block(plaintexts, flags)
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
            principal_id=state.get("principal_id"),
            reranked=state.get("reranked", False),
            dropped_for_budget=state.get("dropped_for_budget", 0),
        )
        return state

    def query(self, query: str, user_role: str, top_k: int = 5, *, principal_id: str | None = None,
              request_id: str | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {"query": query, "user_role": user_role, "top_k": top_k,
                                 "principal_id": principal_id or f"internal:{user_role}"}
        if request_id:
            state["request_id"] = request_id
        return self.graph.invoke(state)


def approx_tokens(text: str) -> int:
    """Rough LLM token estimate (~4 characters per token for English)."""
    return max(1, math.ceil(len(text) / 4))
