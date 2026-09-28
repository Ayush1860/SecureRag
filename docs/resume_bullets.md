# Resume bullets

Every number below comes from `reports/` (see `reports/evaluation.md`) or from checks on the live
deployment (`docs/SCALE_WORKLOG.md`, "Live and verified").

## Campus placement version (paste this)

**SecureRAG — Security-Hardened Enterprise RAG System** | [Live demo](https://ayush1860.github.io/SecureRag/) · [GitHub](https://github.com/Ayush1860/SecureRag)
*Python, FastAPI, LangGraph, ChromaDB/Qdrant, Sentence Transformers, AES-256-GCM, React, Docker, AWS Lambda, GitHub Actions*

- Built a retrieval-augmented generation (RAG) system where each user's role decides what they can
  retrieve: role-based access control (RBAC) is enforced inside the vector store, and document text is
  encrypted with AES-256-GCM, bound to its access labels. **0 data leaks** across 2,400 adversarial queries
  on a corpus of up to **156k encrypted chunks**.
- Designed hybrid search (dense vectors + keyword BM25 with rank fusion and reranking) that reaches
  **nDCG@10 0.699 on BEIR SciFact** (+4.2 points over dense-only). With a Qdrant backend, 95th-percentile
  (p95) retrieval stays **~70 ms for every role at 156k chunks**, where Chroma's most restricted role took
  156 ms.
- Re-architected ingestion from a design that crashed at ~1.1k documents into a streaming, incremental
  pipeline that handles **50k documents**, with ~2 s startup and 1.4 GB peak memory.
- **Deployed it on AWS Lambda for under $1/month:** a container image with the search index encrypted at
  build time, and secrets in SSM. Every push is deployed by GitHub Actions without stored AWS keys (OIDC),
  smoke-tested per role and rolled back automatically on failure. Warm answers take **1–2 s**.

If you have room for only two bullets, keep the first and the last.

## One-line summary (for the "projects" line or a cover note)

Security-hardened RAG system (FastAPI, LangGraph, Qdrant, AES-256-GCM), live on AWS Lambda: role-based access
enforced before generation, 0 leaks in 2,400 adversarial queries at 156k chunks, nDCG@10 0.699 on BEIR SciFact.

## Detailed version (portfolio / longer CV)

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
  canary leak. 185 tests; ruff and mypy clean.
- Deployed serverlessly on AWS: a Lambda container image behind a Function URL, the demo index
  encrypted at build time with the key passed as a BuildKit secret (never in an image layer), and secrets
  loaded from SSM at cold start. The CloudFormation stack uses least-privilege IAM. GitHub Actions deploys
  over OIDC with a per-role smoke test and automatic rollback. The React frontend is on GitHub Pages with a
  strict CSP. Cold start ~11 s, warm answers 1–2 s, about $0.20/month.

## Conservative version

- Re-architected a LangGraph RAG service for scale (streaming ingestion, pluggable Chroma/Qdrant
  store, partitioned BM25) and benchmarked it to 156k encrypted chunks with 0 cross-role leaks.
- Implemented API-key/JWT authentication, AES-GCM encryption with AAD and key rotation, and a
  hash-chained audit log; evaluated retrieval under RBAC on BEIR SciFact and FiQA.
- Deployed it on AWS Lambda with CloudFormation and an OIDC-based GitHub Actions pipeline.

## Be ready to defend

- "0 leaks" means 0 canaries from forbidden documents in answers or context, across 800 queries per run
  at 1k, 10k and 50k docs. It is a test result, not a proof.
- Injection detection with regex alone is ~56% (half the payloads are paraphrased on purpose). Say so if asked.
  The defence is that flagged and unflagged text is always treated as data, not instructions.
- The live demo runs on synthetic documents; the demo keys are public on purpose.
- The Qdrant numbers come from a 4-CPU GitHub runner with no GPU, and the Chroma ones from a laptop with a GPU.
  The fair claim is the shape: Qdrant's p95 is flat across roles (1.05× spread) and across 31k → 156k chunks,
  while Chroma's spread grows to 3.2×. See reports/evaluation.md, section 4.
