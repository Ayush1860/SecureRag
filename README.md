# SecureRAG — Security-Hardened Enterprise RAG

A retrieval-augmented generation service built so that **a caller can never retrieve, decrypt or
see a document outside their role's policy**. It has to stay that way at 150k+ encrypted chunks,
and there are benchmarks to show it.

It combines authenticated RBAC, AES-256-GCM payload encryption with metadata-bound AAD and key
rotation, RBAC-partitioned hybrid retrieval (dense + keyed-hash BM25, RRF, optional cross-encoder),
prompt-injection quarantine, and a hash-chained audit log. The pipeline is orchestrated with
LangGraph and served by FastAPI.

## Architecture

```text
         ┌──────────── Ingestion (offline, streaming, resumable) ─────────────┐
 files ─► loaders (txt/md/pdf/docx/html) ─► labels (sidecar > manifest > folder, fail closed)
         ─► token-aware chunker ─► injection scan (regex [+ ML]) ─► batched embeddings
         ─► AES-256-GCM (key-id tagged, AAD = chunk id + labels) ─► vector store upsert
         ─► HMAC-hashed terms ─► per-partition BM25 rebuild     ─► SQLite state (skip unchanged)
         └────────────────────────────────────────────────────────────────────┘

 Query:  AuthN (API key / JWT → principal → role; dev header only in ENV=dev)
         → validate → retrieve: dense (RBAC-filtered in the store) ∥ sparse (only the role's partitions)
         → RRF → [cross-encoder rerank, authorized chunks only] → authorize
         → fetch + decrypt ONLY the authorized top-k by ID → context token budget → quarantine flagged
         → generate (LLM router: timeouts, retries, fallback chain) → hash-chained audit
```

| Layer | Implementation |
|---|---|
| API | FastAPI: `/api/query`, `/api/query/stream` (SSE), `/api/audit`, `/api/admin/ingest`, `/api/health`, `/api/ready` |
| Orchestration | LangGraph: validate → retrieve → rerank? → authorize → decrypt_sanitize → generate → audit |
| Vector store | `VectorStore` protocol: Chroma (default) or Qdrant (`VECTOR_BACKEND=qdrant`, payload indexes) |
| Sparse | bm25s, one index per (department, clearance), HMAC-SHA256 terms, per-partition IDF |
| Crypto | AES-256-GCM, keyring with key IDs, `v2:<kid>:…` blobs, AAD, resumable rotation |
| LLMs | Groq / OpenAI / Anthropic / Gemini with fallback chain; deterministic `mock` for offline use |
| UI | React (Vite) dashboard; Streamlit demo (dev only) |

## Results

All numbers are from `reports/` and measured on 8 CPUs with a GTX 1650 (embeddings on GPU),
using `all-MiniLM-L6-v2` and the mock LLM, so latency excludes generation. Full tables:
[`reports/evaluation.md`](reports/evaluation.md).

**Security at scale** (synthetic corpus with a canary in every confidential doc; 200 queries per role):

| Docs | Chunks | Canary leaks | Role-spoof escalations | Injection detection (regex only) | Injection FPR |
|---|---|---|---|---|---|
| 1,000 | 3,101 | **0** | **0** / 25 | 58.3% | 0.00% |
| 10,000 | 31,429 | **0** | **0** / 25 | 55.7% | 0.00% |
| 50,000 | 155,860 | **0** | **0** / 25 | 56.8% | 0.00% |

Half the planted injections are deliberately paraphrased to dodge the regex heuristics. The
detection rate is an honest lower bound, and the reason `INJECTION_CLASSIFIER` exists.

**Scale** (see [plots](reports/scale/phase6_scale.png)):

| Docs | Chunks | Ingest chunks/s | Ingest peak RSS | Startup: store + key check | Retrieval p95 (exec / employee / guest) |
|---|---|---|---|---|---|
| 1,000 | 3,101 | 84 | 1.07 GB | 1.14 s | 21 / 25 / 19 ms |
| 10,000 | 31,429 | 120 | 1.17 GB | 1.36 s | 28 / 36 / 47 ms |
| 50,000 | 155,860 | 114 | 1.41 GB | 2.04 s | 49 / 99 / 156 ms |

Before this work, ingest **crashed at ~1,150 docs** (a single Chroma batch over the 5,461 limit).
Startup decrypted the whole corpus into RAM, and a wrong key triggered a silent full rebuild
([baseline](reports/scale/baseline.md)).

**Retrieval quality under RBAC** (BEIR; each role scored only on the relevant docs it may see;
`exec` sees everything, so it compares to published numbers). nDCG@10:

| Dataset (exec) | Dense | Sparse (HMAC BM25) | Hybrid RRF | Hybrid + rerank |
|---|---|---|---|---|
| SciFact (5.2k docs) | 0.657 | 0.620 | 0.682 | **0.699** |
| FiQA (57.6k docs) | 0.375 | 0.223 | 0.342 | **0.376** |

On FiQA the lexical side is weak, so plain RRF falls below dense-only; the cross-encoder recovers
it. Leaks across all 16 role × mode combinations on both datasets: **0**.

## Quick start (local, dev mode)

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # ENV=dev AUTH_MODE=dev for the demo role switcher
python scripts/generate_key.py                        # paste into SECURERAG_AES_KEY_B64 in .env
python scripts/ingest.py                              # encrypt + index data/sample (incremental)
cd frontend && npm ci && npm run build && cd ..
uvicorn app.api:app --port 8000                       # http://localhost:8000 (docs at /docs)
```

The dev-only Streamlit demo: `streamlit run app/streamlit_app.py`.

### Production-style

```bash
# .env: ENV=prod, AUTH_MODE=api_key (or jwt + JWT_SECRET), SECURERAG_AES_KEY_B64 or a keyring
docker compose run --rm ingest                        # index DATA_DIR into Qdrant
docker compose run --rm api python scripts/create_api_key.py --principal alice --role employee
docker compose up -d api
curl -H "X-API-Key: <key>" -H "Content-Type: application/json" \
     -d '{"query": "What is the onboarding policy?"}' http://localhost:8000/api/query
```

## Roles

| Role | Max clearance | Departments |
|---|---|---|
| `guest` | public | general |
| `employee` | internal | general, engineering, hr |
| `finance_lead` | confidential | general, finance |
| `exec` | confidential | general, engineering, hr, finance, exec |
| `admin` | – | none (may read the audit log and run ingestion jobs; sees no documents) |

## Operations

| Task | Command |
|---|---|
| Incremental ingest / dry run / rebuild | `python scripts/ingest.py [--dry-run] [--full-rebuild] [--workers N]` |
| Create an API key | `python scripts/create_api_key.py --principal NAME --role ROLE` |
| Rotate the encryption key | add a key to the keyring, make it active, then `python scripts/rotate_key.py` |
| Verify the audit chain | `python scripts/verify_audit.py` |
| Background ingest over the API | `POST /api/admin/ingest` as `admin`, then `GET /api/admin/ingest/{job_id}` |

Configuration: every setting is an environment variable; see [`.env.example`](.env.example).
Security model and residual risks: [`SECURITY.md`](SECURITY.md).

## Reproduce the benchmarks

```bash
python scripts/benchmark_scale.py --docs 1000   --label phase6_docs_1000
python scripts/benchmark_scale.py --docs 10000  --label phase6_docs_10000
python scripts/benchmark_scale.py --docs 50000  --label phase6_docs_50000 --timeout 7200
python scripts/eval_beir.py --datasets scifact fiqa
python scripts/plot_scale.py --prefix phase6
python scripts/evaluate.py            # rebuilds reports/evaluation.md
```

## Tests and CI

```bash
pytest -q          # 163 tests, offline (mock LLM, fake encoder), plus one real-model suite on data/sample
ruff check securerag app scripts tests && mypy securerag
```

GitHub Actions runs lint, type checks and the tests, then a 300-doc scale run that fails on any
canary leak, then the frontend build.

The development history is in [`docs/SCALE_WORKLOG.md`](docs/SCALE_WORKLOG.md): what changed in
each phase, why, and what was tried and dropped.
