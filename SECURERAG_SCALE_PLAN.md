# SecureRAG — Scale & Robustness Plan (Claude Code Prompt Pack)

Repo: `https://github.com/Ayush1860/SecureRag`
Goal: take SecureRAG from a 5-document demo to a system that ingests and serves **100k–1M+ chunks** without losing any of its security guarantees, and prove it with real benchmarks.

How to use this file: drop it in the repo root, start Claude Code, paste the **Session Opener** once, then run one **Phase prompt** at a time. Each phase ends with passing tests and one commit. Don't run two phases in one session.

---

## 1. Audit summary (why the current code won't scale)

| # | Location | Problem | Impact at scale |
|---|----------|---------|-----------------|
| 1 | `retrieval/store.py::build_store` | Loads every doc into a list, encodes all chunks in one `encoder.encode` call, single `collection.add` | OOM on large corpora; Chroma rejects batches above `client.get_max_batch_size()` (~5k) |
| 2 | `build_store` | `delete_collection` on every ingest, `uuid4` IDs | No incremental ingest, no dedup, re-ingest = full rebuild |
| 3 | `retrieval/store.py::load_store` | `collection.get()` of the whole corpus + decrypts **every** chunk at startup, keeps plaintext in RAM | Startup time and memory grow linearly; also defeats encryption-at-rest (entire corpus is plaintext in process memory) |
| 4 | `load_store` | On `DecryptionError`, silently rebuilds the store from `DATA_DIR` | A wrong key triggers an hours-long rebuild and hides a real security event |
| 5 | `retrieval/hybrid.py` | `BM25Okapi` in memory, `get_scores` over the full corpus, then Python loop over all chunks for RBAC | O(N) per query in pure Python; rebuilt on every startup |
| 6 | `hybrid.py::retrieve` | Dense candidate pool = `top_k * 2` | RRF has almost nothing to fuse; recall drops on big corpora |
| 7 | `ingestion/loader.py` | Only `*/*/*.txt`, metadata from folder names, fixed 800-char split | No PDF/DOCX/MD/HTML, splits mid-word/sentence, no doc IDs or chunk indexes |
| 8 | `app/api.py::QueryRequest` | **Role comes from the request body** | Anyone can send `"role": "exec"` — RBAC is bypassable. Must fix before calling this secure |
| 9 | `app/api.py` | `CORS *` with credentials, raw exception text in 500s | Security + info leak |
| 10 | `llm/providers.py::call_llm` | New client per call, no timeout, no retry, no fallback | One slow provider stalls the API |
| 11 | `security/audit.py` | Plain JSONL append, full-file read for recent events, called "immutable" | Not tamper-evident, not multi-worker safe, O(N) reads |
| 12 | `security/encryption.py` | Single key, no key ID in ciphertext | No rotation path |
| 13 | `security/sanitizer.py` | Regex scan runs on every query for every chunk | Wasted latency; should be done once at ingest |
| 14 | `config.py` | Frozen dataclass reading env at import | No validation, hard to override in tests |
| 15 | `reports/evaluation.md` | 100% recall on 5 docs | Not a meaningful benchmark; interviewers will notice |
| 16 | Repo | No Docker, no CI, two Streamlit entrypoints + React frontend | Hard to run, duplicated UI |

Also: plaintext embeddings are stored in Chroma. Embedding-inversion attacks can partially reconstruct text from vectors. Document this as a known limitation (Phase 4).

---

## 2. Target architecture

```text
            ┌───────────── Ingestion (offline, streaming, resumable) ─────────────┐
 files ──►  loaders (txt/md/pdf/docx/html) ─► manifest metadata ─► token-aware chunker
            ─► content-hash IDs ─► ingest-time injection scan ─► batched embeddings
            ─► AES-GCM (key_id-tagged) ─► vector store upsert + sparse index shard
            └──► ingestion state DB (SQLite): doc hash, status, chunk IDs ─────────┘

 Query:  AuthN (JWT/API key → role server-side)
         → validate → retrieve (dense ∥ sparse, RBAC pre-filter, pool=50)
         → RRF → optional cross-encoder rerank → authorize (post-filter)
         → fetch + decrypt ONLY top-k by ID → context token budget
         → generate (timeout/retry/fallback) → hash-chained audit
```

Key design decisions:
- **Never hold the corpus plaintext in memory.** Decrypt only the final top-k chunks per request.
- **Deterministic chunk IDs** = `sha256(doc_id + chunk_index + chunk_text)[:32]` → idempotent upserts.
- **Sparse index is partitioned by `(department, clearance)`**, so RBAC filtering is structural, not a Python loop. Tokens are stored as keyed HMAC hashes so the on-disk index doesn't expose vocabulary.
- **Vector store behind an interface** (`VectorStore` protocol) with Chroma as default and Qdrant as the scale backend (payload indexes, native sparse vectors, multi-process safe).

---

## 3. Session Opener (paste once at the start of every Claude Code session)

```text
You are working on SecureRAG, a security-hardened RAG system (FastAPI + LangGraph +
ChromaDB + BM25 + AES-256-GCM + RBAC + audit logging). Read SECURERAG_SCALE_PLAN.md
in the repo root first; it is the source of truth for this work.

Working rules:
1. Start in plan mode. Read the files the phase touches, then show me a short plan
   (files to change, new files, new deps, test strategy) and wait for my approval.
2. Security invariants must never regress:
   - A role must never retrieve, decrypt, or see a chunk outside its RBAC policy.
   - Ciphertext at rest stays AES-256-GCM with random 12-byte nonces.
   - Retrieved text is always treated as data, never instructions.
   If a change could weaken any of these, stop and tell me.
3. Keep LLM_PROVIDER=mock working offline. All tests must run without network or API keys.
4. Run `pytest -q` before and after changes. Existing tests may be updated only when
   the behaviour change is intentional; say which ones and why.
5. Add new dependencies to both requirements.txt and pyproject.toml with version bounds.
6. Type hints on all new public functions. No print() in library code; use logging.
7. Finish with: summary of changes, test results, and a conventional commit message.
   Do not push.
```

---

## 4. Phase prompts

### Phase 0 — Scale harness and baseline (do this first)

```text
Phase 0: build a scale benchmark harness BEFORE changing any pipeline code, so we can
measure improvements.

1. Create scripts/generate_corpus.py:
   - Generates a synthetic corpus under data/synthetic/<department>/<clearance>/
     with configurable --docs N (default 2000) and --avg-words.
   - Departments: general, engineering, hr, finance, exec. Clearances: public,
     internal, confidential. Realistic-ish text (seeded Faker or templated
     paragraphs), deterministic with --seed.
   - Plants "canary" sentences in confidential docs (unique tokens like
     CANARY-FIN-00017) so leak tests can grep answers and contexts for them.
   - Plants ~2% docs with prompt-injection payloads for sanitizer evaluation.
2. Create scripts/benchmark_scale.py that, for a given DATA_DIR, reports:
   ingest wall time, chunks/sec, peak RSS (psutil), Chroma dir size on disk,
   API startup time, p50/p95/p99 query latency over 200 queries per role,
   and canary leak count (must be 0). Output JSON + markdown to reports/scale/.
3. Run it at 500 and 2000 docs on the CURRENT code and save as
   reports/scale/baseline.md. If it crashes (e.g. Chroma batch limit), record the
   failure point; that is a valid baseline.
4. Add data/synthetic/ to .gitignore.

Acceptance: baseline report exists; no pipeline code changed.
```

### Phase 1 — Config and ingestion pipeline

```text
Phase 1: make ingestion streaming, incremental, multi-format and idempotent.

1. Replace securerag/config.py with pydantic-settings (BaseSettings, env prefix
   optional). Add fields: embed_model, embed_batch_size, embed_device (auto/cpu/cuda),
   chunk_size_tokens (default 400), chunk_overlap_tokens (60), ingest_batch_size,
   dense_candidates (50), sparse_candidates (50), rerank_enabled (False),
   context_token_budget (3000), state_db_path. Keep all current env var names working.
   Expose get_settings() with lru_cache; tests can override.
2. securerag/ingestion/:
   - loaders.py: pluggable loaders registered by extension: .txt, .md, .pdf (pypdf),
     .docx (python-docx), .html (BeautifulSoup text). Unknown types are skipped
     with a warning, never crash the run.
   - metadata.py: metadata resolution order: sidecar `<file>.meta.yaml` > a
     `manifest.csv` at DATA_DIR root > folder convention <department>/<clearance>/.
     Validate department/clearance against rbac.py; files with invalid or missing
     labels are REJECTED (fail closed), logged, and counted. Never default to public.
   - chunker.py: token-aware recursive splitter (paragraph → sentence → word) using
     the embedding model's tokenizer for length. Returns chunk_index and char offsets.
   - Each chunk gets metadata: doc_id (sha256 of relative path), chunk_index,
     content_hash, source, department, clearance, key_id, embed_model, ingested_at,
     injection_flagged (bool, see Phase 4; stub as False for now).
3. securerag/ingestion/state.py: SQLite table documents(doc_id, path, file_hash,
   status, chunk_count, updated_at). Ingest skips unchanged files, re-ingests changed
   ones (delete old chunk IDs first), and removes chunks for deleted files.
4. securerag/ingestion/pipeline.py: generator-based pipeline. Read → chunk → embed in
   batches of embed_batch_size → encrypt → upsert in batches no larger than
   client.get_max_batch_size(). Chunk IDs deterministic (see plan). Memory must stay
   flat as corpus grows. Show a tqdm progress bar in the CLI only.
5. scripts/ingest.py becomes a CLI (argparse or typer):
   `ingest --data-dir X [--full-rebuild] [--dry-run] [--workers N]`.
   Remove delete_collection from the default path; only --full-rebuild wipes.
6. Tests: chunker boundaries and overlap, metadata precedence and fail-closed
   rejection, idempotency (ingest twice → same count, same IDs), incremental update
   (modify one file → only its chunks change), deletion handling.

Acceptance: benchmark_scale.py at 2000 docs completes; peak RSS during ingest does
not grow meaningfully between 500 and 2000 docs; all tests pass.
```

### Phase 2 — Storage layer: no full-corpus decrypt, pluggable vector store

```text
Phase 2: remove the startup full-corpus load and decrypt.

1. Define securerag/retrieval/vector_store.py with a VectorStore Protocol:
   upsert(ids, embeddings, payloads, metadatas), delete(ids), query(embedding, n,
   where) -> list[(id, score)], get(ids) -> list[(id, payload, metadata)], count().
   Implement ChromaVectorStore (current behaviour, batched). Add QdrantVectorStore
   behind an optional extra (qdrant-client), with payload indexes on department and
   clearance. Backend chosen by VECTOR_BACKEND env var.
2. Rewrite load_store: open the store, verify the key by decrypting a small random
   sample (e.g. 5 chunks). On DecryptionError: raise a clear startup error and exit.
   REMOVE the automatic rebuild. Rebuild only through `ingest --full-rebuild`.
3. Chunk dataclass no longer carries plaintext. Retrieval returns IDs + metadata;
   the decrypt_sanitize node fetches payloads by ID for the final top-k only and
   decrypts them. Plaintext lives only within one request.
4. Load the SentenceTransformer once, on the configured device, and share it.
5. Tests: startup with wrong key fails fast (no rebuild); plaintext is not held on
   the retriever/store objects after startup (assert via attribute inspection);
   both backends pass the same retrieval tests (Qdrant via in-memory mode).

Acceptance: API startup time at 2000 docs is roughly constant vs 500 docs.
```

### Phase 3 — Scalable hybrid retrieval

```text
Phase 3: replace in-memory BM25Okapi with a persistent, partitioned, fast sparse index,
and improve fusion quality.

1. securerag/retrieval/sparse.py using the `bm25s` library:
   - One index per (department, clearance) partition, built during ingestion,
     persisted under SPARSE_DIR.
   - Tokenise, then map each token to HMAC-SHA256(index_key, token)[:16 hex] before
     indexing and querying. index_key is derived from the AES key via HKDF with a
     distinct info label. Scores are identical to plaintext BM25 but the on-disk
     index doesn't expose vocabulary. Document in SECURITY.md that term-frequency
     analysis is still possible.
   - Query only the partitions the role is allowed to see (from rbac.py). Merge by
     score. Note in code comments that IDF is per-partition, which is deliberate:
     a global IDF would leak statistics from confidential partitions.
   - Incremental updates: rebuild only the partitions touched by an ingest run.
2. hybrid.py: dense and sparse candidate pools from settings (default 50 each),
   run concurrently (ThreadPoolExecutor). RRF as today. Keep the post-fusion
   allow-list check.
3. Optional reranker stage (rerank_enabled): cross-encoder
   `BAAI/bge-reranker-base` (configurable) over fused top-N (default 30) → top_k.
   New LangGraph node `rerank` between retrieve and authorize, skipped when disabled.
4. Context budget: in decrypt_sanitize, add chunks in rank order until
   context_token_budget is reached; report dropped count in state.
5. Make the embedding model configurable, default stays all-MiniLM-L6-v2; document
   bge-small-en-v1.5 / e5-small-v2 as recommended upgrades. Store embed_model in
   metadata and refuse to query a collection built with a different model.
6. Tests: partition isolation (guest query never touches confidential partition
   files, verify via spy), HMAC index gives same top results as plaintext BM25 on a
   fixture, reranker toggles, token budget truncation, model mismatch error.

Acceptance: p95 retrieval latency at 2000 docs within 2x of 500 docs.
```

### Phase 4 — Security hardening that holds at scale

```text
Phase 4: close the real security gaps.

1. Authentication (highest priority):
   - Remove `role` from QueryRequest. Role comes from the authenticated principal.
   - Support two modes via AUTH_MODE: `api_key` (keys in a hashed keys file mapping
     key → principal → role) and `jwt` (HS256/RS256, role claim, exp checked).
   - A `dev` mode keeps the demo UI working with a role switcher, but only when
     ENV=dev, and logs a loud warning at startup.
   - Update the React frontend and Streamlit app accordingly.
2. Encryption:
   - Ciphertext format v2: `v2:<key_id>:<b64(nonce||tag||ct)>`. Decrypt v1 blobs
     for backward compatibility.
   - Keyring loaded from env/file: multiple keys, one active. Add
     scripts/rotate_key.py that re-encrypts chunks in batches to the active key,
     resumable via the state DB.
   - Bind metadata as GCM associated data (AAD = chunk_id + department + clearance)
     so a ciphertext moved to a different chunk/label fails authentication.
3. Injection scanning at ingest: run sanitizer once per chunk during ingestion and
   store injection_flagged in metadata. At query time use the stored flag (still
   wrap flagged chunks). Add an optional ML detector (e.g. a small prompt-injection
   classifier from Hugging Face) behind INJECTION_CLASSIFIER, used at ingest only.
4. Audit log:
   - Each event includes prev_hash and entry_hash (sha256 over canonical JSON of the
     event + prev_hash) → tamper-evident chain. Add scripts/verify_audit.py.
   - Size-based rotation; the chain continues across files.
   - Cross-process safe writes (file lock via `filelock`) or move to SQLite WAL.
   - read_recent_audit_events reads from the end of the file, not the whole file.
   - Log principal ID (not just role).
5. API: restrict CORS to CORS_ORIGINS env; generic 500 messages with request_id
   (details only in server logs); request size limits; per-principal rate limiting
   (slowapi).
6. SECURITY.md: threat model table (assets, attacker, control, residual risk),
   including embedding inversion and HMAC frequency analysis as known limitations.
7. Tests: role spoofing via body is impossible; expired/forged JWT rejected;
   AAD tamper test (swap metadata → decrypt fails); rotation round-trip; audit chain
   verification catches an edited line.
```

### Phase 5 — Runtime robustness and deployment

```text
Phase 5: make the service production-shaped.

1. LLM layer: one cached client per provider; timeouts (configurable, default 30s);
   retries with exponential backoff on 429/5xx (tenacity); ordered fallback list
   (e.g. groq → gemini → mock-refusal). Record provider actually used and retry
   count in state and audit.
2. Optional streaming endpoint POST /api/query/stream (SSE) — audit written after
   stream completes.
3. /api/health (liveness) and /api/ready (store opened, key verified, sparse
   indexes loaded, embedder warm).
4. Structured JSON logging with request_id on every line.
5. Background ingestion: POST /api/admin/ingest (admin role only) triggers an
   ingestion job; GET /api/admin/ingest/{job_id} returns progress from the state DB.
6. Dockerfile (multi-stage, non-root user, model cached at build time) and
   docker-compose.yml with api + qdrant services and volumes for data.
7. GitHub Actions: lint (ruff), type check (mypy on securerag/), pytest,
   and a small benchmark_scale.py run at 300 docs that fails if canary leaks > 0.
8. Consolidate UIs: keep the React frontend as primary; keep one Streamlit entry
   point or remove it (ask me).
```

### Phase 6 — Evaluation at real scale

```text
Phase 6: replace the toy benchmark with defensible numbers.

1. Retrieval quality on public datasets via the `ir_datasets` or `beir` loaders:
   SciFact and FiQA (and NFCorpus if time allows). Assign synthetic RBAC labels to
   documents deterministically (seeded), and evaluate per role against only the
   qrels that role may see. Report nDCG@10, Recall@10/50, MRR@10 for:
   dense only, sparse only, hybrid RRF, hybrid + rerank.
2. Security at scale on the synthetic corpus from Phase 0 at 10k and 50k docs:
   canary leak rate per role (must be 0), injection detection rate and FPR, and
   role-spoof attempts (must be 100% rejected).
3. Performance: ingest throughput, index size, startup time, p50/p95/p99 query
   latency, peak RSS, at 1k / 10k / 50k docs. Plot with matplotlib into
   reports/scale/.
4. Regenerate reports/evaluation.md from these runs. Remove the old "100% recall"
   headline; keep the small fixture only as a unit test.
5. Update README: architecture diagram, scale numbers table, how to reproduce each
   benchmark with one command.
```

---

## 5. Suggested order and time budget

| Phase | Priority | Rough effort | Why this order |
|-------|----------|--------------|----------------|
| 0 | Must | 0.5 day | Without a baseline you can't claim improvement |
| 4.1 (auth only) | Must | 0.5 day | Role-in-body is the one bug an interviewer can break in 10 seconds |
| 1 | Must | 1–1.5 days | Everything else depends on streaming, idempotent ingest |
| 2 | Must | 1 day | Removes the startup full decrypt (scale + security) |
| 3 | Must | 1–1.5 days | Makes retrieval sublinear-ish and improves quality |
| 4 (rest) | Should | 1 day | Rotation, AAD, audit chain — strong interview material |
| 6 | Must | 1 day | Real numbers for resume bullets |
| 5 | Nice | 1 day | Docker/CI polish |

Run Phase 4.1 on its own right after Phase 0 with this prompt: *"Do only step 1 (Authentication) of Phase 4 from SECURERAG_SCALE_PLAN.md."*

---

## 6. Definition of done

- Ingest 50k+ docs with flat memory, resumable, idempotent.
- API startup time independent of corpus size; no corpus plaintext held in memory.
- Role is derived from authentication, never from the request body.
- 0 canary leaks across all roles at 50k docs.
- Retrieval quality reported on at least two public datasets with ablations.
- Tamper-evident audit log with a verification script.
- One-command Docker run and green CI.

---

## 7. Resume bullets to write *after* Phase 6 (fill in real numbers)

- Scaled a security-hardened RAG pipeline to __k chunks with streaming, idempotent ingestion (__ chunks/s) and constant-time startup by eliminating full-corpus decryption.
- Built partitioned, HMAC-tokenised BM25 + dense hybrid retrieval with cross-encoder reranking; nDCG@10 __ on SciFact (+__ over dense-only).
- Enforced JWT-based RBAC with dual-stage filtering and AES-256-GCM with metadata-bound AAD and key rotation; 0 canary leaks across __k adversarial queries.
