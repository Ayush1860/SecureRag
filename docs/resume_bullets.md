# Resume bullets

**SecureRAG — Security-Hardened Enterprise RAG System**
*Python, FastAPI, LangGraph, ChromaDB/Qdrant, bm25s, Sentence Transformers, AES-256-GCM, React, Docker*

Every number below comes from `reports/` (see `reports/evaluation.md`).

- Scaled a security-hardened RAG pipeline from a design that crashed at ~1.1k documents to
  **50k docs / 156k encrypted chunks**. Ingestion is streaming, incremental and idempotent
  (~114 chunks/s on a GTX 1650, 1.4 GB peak RAM), and the store-dependent startup is **~2 s**
  because the full-corpus decryption at boot is gone.
- Built **RBAC-partitioned hybrid retrieval**: dense search filtered inside the store via
  partition-prefixed IDs, plus HMAC-tokenised per-partition BM25, fused with RRF and an optional
  cross-encoder. That gives nDCG@10 **0.699 on SciFact** (+4.2 over dense-only) and cuts
  filtered-role p95 retrieval **2.8×** at 31k chunks (102 → 36 ms).
- Enforced **authenticated RBAC** (hashed API keys / JWT, role never taken from the request body)
  and **AES-256-GCM with metadata-bound AAD and resumable key rotation**. **0 canary leaks** across
  2,400 adversarial and topical queries at up to 50k docs, **0 leaks** across all role × mode
  BEIR runs, and 100% of role-spoofing attempts blocked.
- Added a tamper-evident hash-chained audit log, SSE streaming, an LLM router (timeouts, retries,
  provider fallback), readiness probes, multi-stage non-root Docker, and CI that fails on any
  canary leak. 163 tests; ruff and mypy clean.

## Conservative version

- Re-architected a LangGraph RAG service for scale (streaming ingestion, pluggable Chroma/Qdrant
  store, partitioned BM25) and benchmarked it to 156k encrypted chunks with 0 cross-role leaks.
- Implemented API-key/JWT authentication, AES-GCM encryption with AAD and key rotation, and a
  hash-chained audit log; evaluated retrieval under RBAC on BEIR SciFact and FiQA.
