# Scale & Robustness Work Log

Running log for the work in [`SECURERAG_SCALE_PLAN.md`](../SECURERAG_SCALE_PLAN.md).
Each phase lists what changed, why, any deviations from the plan, test results and
benchmark numbers. Newest phase at the bottom.

## Environment

- Checkout: `D:\SecureRag-main` (fresh clone of `github.com/Ayush1860/SecureRag`, base commit `e79094b`).
- Python 3.11.4 on Windows 11, 8 logical CPUs, CUDA GPU available to torch 2.2.2+cu121.
- Project venv: `.venv` created with `--system-site-packages`, so it reuses the globally
  installed torch / sentence-transformers / chromadb and only adds new packages
  (pypdf, python-docx, beautifulsoup4, bm25s, qdrant-client, slowapi, PyJWT, ruff, mypy).
  Run everything with `.venv/Scripts/python`.
- `USE_TF=0` must be set before importing transformers on this machine (Keras 3 is installed
  globally and breaks the TF code path). `securerag/config.py` already sets it.

---

## Phase 0 — Scale harness and baseline

**Goal:** measure the current pipeline before changing it.

### Added
- `scripts/generate_corpus.py` — deterministic synthetic corpus
  (`data/synthetic/<name>/<department>/<clearance>/doc_NNNNNN.txt` + `manifest.json`).
  Seeded `random.Random` + department-specific sentence templates (no Faker dependency).
  Every confidential doc carries a unique canary (`CANARY-FIN-000017`); ~2% of docs carry a
  prompt-injection payload. The manifest records each doc's labels, canary and injection flag.
- `scripts/benchmark_scale.py` — runs ingest and serving in separate subprocesses and samples
  RSS with psutil. Reports ingest time, chunks/s, peak RSS, store size on disk, engine startup
  time, p50/p95/p99 end-to-end and retrieval latency per role (200 queries/role, mock LLM) and
  canary leak count. Query mix per role: 50% topical, 30% canary probes, 20% injection-style
  probes asking for other tiers' codes. A canary counts as a leak if it appears in the answer
  or context excerpts of a role that `rbac.authorize` would deny for that canary's document.
  Pipeline APIs are touched only in `_ingest_corpus` / `_open_engine` so later phases update
  one place. Output: `reports/scale/<label>.{json,md}`.
- `tests/test_scale_harness.py` — determinism, layout, canary placement, query builder, report rendering.
- `reports/scale/baseline.md` — baseline summary.

### Changed
- `.gitignore`: `data/synthetic/`, `data/bench/` (benchmark scratch stores).
- `requirements.txt`: `psutil`.
- `pyproject.toml`: added `[project] dependencies` mirroring `requirements.txt` (user chose option (a)),
  `dev` extra for pytest, and `packages.find include=["securerag*"]` so `pip install .` doesn't trip
  over the flat layout.

### Decisions
- Benchmark data lives in `data/bench/<label>` and is deleted after each run (`--keep-work` keeps it),
  so the real `data/chroma_db` is never touched.
- Each run uses a fresh random AES key passed via env to both subprocesses.
- Engine startup = `load_store` + `HybridRetriever` + `SecureRAG` construction in a fresh process,
  which includes embedding-model load (~10 s on this machine). That fixed cost is the same at every size.

### Results
See [`reports/scale/baseline.md`](../reports/scale/baseline.md). I added a 1000-doc run so the
old code's scaling shows between the two sizes it can handle.
- 500 docs: ingest OK (2,344 chunks, 24.7 s), startup 13.7 s, query p95 52 ms, 0 leaks.
- 1000 docs: ingest OK (4,685 chunks), query p95 63 ms, 0 leaks.
- 2000 docs: **ingest crashes** with Chroma `Batch size of 9412 is greater than max batch size of 5461`.
  This is the recorded failure point.

### Tests
`pytest -q`: 42 passed (38 existing + 4 new). No pipeline code was changed.

**Commit:** `chore(bench): add synthetic corpus generator and scale benchmark harness`

---

## Phase 1 — Config and ingestion pipeline

**Goal:** streaming, incremental, multi-format, idempotent ingestion.

### Added
- `securerag/config.py`: rewritten on pydantic-settings. The old env var names still work
  (`DATA_DIR`, `CHROMA_DIR`, `AUDIT_LOG_PATH`, `TOP_K`, `FUSION_K`, `LLM_PROVIDER`), and all new
  fields from the plan are there. It validates overlap < chunk size and uses `get_settings()`
  with `lru_cache`. `load_dotenv()` stays because the AES key and provider API keys are still
  read from `os.environ`.
- `securerag/ingestion/loaders.py`: extension registry for `.txt/.md/.markdown/.pdf/.docx/.html/.htm`.
  Parser libraries are imported lazily. Parse errors raise `LoaderError`, which the pipeline counts
  as `failed` without crashing.
- `securerag/ingestion/metadata.py`: labels come from the sidecar `<file>.meta.yaml`, then
  `manifest.csv`, then the `<department>/<clearance>/` folder convention. They're validated against
  `rbac.DEPARTMENTS` / `CLEARANCE_LEVELS`. Missing or invalid labels mean the file is **rejected**
  (fail closed); nothing defaults to public.
- `securerag/ingestion/chunker.py`: recursive splitter (paragraph, then sentence, then word, then
  characters) with token-length packing. Overlap lands on unit boundaries, and chunks carry char
  offsets and token counts.
- `securerag/ingestion/state.py`: SQLite (WAL) tables `documents`, `chunks`, `runs` and `meta`.
- `securerag/ingestion/pipeline.py`: `IngestPipeline`, a bounded-memory streaming pipeline with a
  bounded thread pool for parsing (`--workers`). It embeds in `embed_batch_size` batches and
  upserts in batches no larger than Chroma's `get_max_batch_size()`.
- `securerag/retrieval/vector_store.py`: `VectorStore` protocol + `ChromaVectorStore` (batched
  upsert/delete, `iter_all` pagination, store info kept in collection metadata). Pulled forward
  from Phase 2 because the pipeline needs batched writes.
- `securerag/retrieval/embedder.py`: `get_encoder()` caches the model per (model, device), and
  `auto` picks CUDA when available (Phase 2 item 4, pulled forward).
- `scripts/ingest.py`: argparse CLI (`--data-dir`, `--chroma-dir`, `--full-rebuild`, `--dry-run`,
  `--workers`, `--json`) with a tqdm bar. `delete_collection` now only runs on `--full-rebuild`.
- `tests/test_ingestion.py` (25 tests): chunker bounds/offsets/overlap/sentence boundaries/oversized
  units; metadata precedence and 5 fail-closed cases; loaders (txt/md/html/docx, unknown, corrupt
  pdf); pipeline idempotency, incremental update, deletion, relabel, reject-after-indexed,
  unsupported files, Chroma batch limit, dry run, full rebuild, wrong-key refusal, and parallel
  vs serial producing the same IDs.

### Changed
- `securerag/retrieval/store.py`: `build_store` now means "full rebuild via the pipeline, then
  load". `load_store` pages through the store. Both keep their signatures so the API, Streamlit
  app and evaluation runner work unchanged; Phase 2 replaces `load_store`.
- `securerag/security/encryption.py`: added `derive_subkey()` (HKDF-SHA256), `key_id`
  (non-secret key fingerprint), and `keyed_hash()` (HMAC-SHA256).
- `securerag/security/rbac.py`: `DEPARTMENTS` constant.
- `scripts/benchmark_scale.py`: ingest adapter now calls `run_ingestion(..., full_rebuild=True)`.
- deps: pydantic-settings, PyYAML, pypdf, python-docx, beautifulsoup4, tqdm.

### Deviations from the plan (and why)
- **Chunk IDs, content hashes and file fingerprints are keyed (HMAC) rather than plain SHA-256.**
  A plain `sha256(doc_id + index + text)` is derived from plaintext. Anyone who can read the store
  and guess a chunk (e.g. "salary of X is N" for a range of N) could confirm the guess offline.
  With HMAC under a key derived from the AES key, the IDs are still deterministic and idempotent
  but can't be used for that. `doc_id` stays `sha256(relative path)`, since the file name is
  already stored in plaintext as `source`.
- **Chunk size is capped at the model's window.** `all-MiniLM-L6-v2` embeds at most 256 tokens,
  so the plan's default of 400 would silently truncate each chunk's second half from the dense
  index. The pipeline caps chunks at `max_seq_length - 2` (254) and logs a warning. The 400 default
  applies as-is to longer-context models.
- **`injection_flagged` is computed now,** not stubbed. It costs one regex pass per chunk at ingest.
  Query time still re-checks until Phase 4 switches to the stored flag.
- **The file fingerprint includes labels, embed model and chunker params,** not just file bytes,
  so relabelling through a sidecar or manifest, or changing the chunker/model, re-indexes the doc.
- **Crash safety:** new chunk IDs are recorded in the state DB before they are written to the
  store, and stale ones are deleted after. A crash at any point leaves chunks that the next run
  cleans up, with no orphans in the store.
- **Store/state/key consistency checks:** the store keeps `embed_model` and `index_key_id` in its
  collection metadata. Ingesting into a non-empty store with a different key or model is refused
  with a pointer to `--full-rebuild`. A state DB whose store is empty, or that belongs to another
  store/key, is reset automatically. That only causes re-processing and can never skip anything.
- **The ingest CLI refuses to run without `SECURERAG_AES_KEY_B64`.** The old script ingested with
  a throwaway key and printed it to stdout. The in-process `build_store` used by tests and the
  evaluation runner still accepts an ephemeral key.
- The state DB defaults to `<CHROMA_DIR>_state.sqlite` (e.g. `data/chroma_db_state.sqlite`), so
  every store gets its own state; `STATE_DB_PATH` overrides this.

### Results (`reports/scale/phase1_docs_*.md`)
| Metric | 500 docs | 1000 docs | 2000 docs |
|---|---|---|---|
| Ingest | OK | OK | **OK** (was: crash) |
| Chunks (254-token recursive chunks) | 1,546 | 3,101 | 6,233 |
| Ingest wall time (s) | 24.1 | 36.0 | 60.2 |
| Ingest peak RSS (MB) | 1,036 | 1,051 | 1,066 |
| Query p95 (ms) | 35.1 | 55.0 | 102.9 |
| Canary leaks | 0 | 0 | 0 |

**Acceptance check:** 2000 docs complete. Peak ingest RSS grows 3% from 500 to 2000 docs
(1,036 → 1,066 MB, mostly the model's ~1 GB baseline), so memory is flat. Query latency still
grows linearly because serving still uses the old in-memory BM25 over the whole decrypted
corpus. Phases 2 and 3 address that.

### Tests
`pytest -q`: 67 passed (42 before + 25 new). No existing tests were modified.

**Commit:** `feat(ingest): streaming, incremental, multi-format ingestion pipeline`

---

## Phase 2 — Storage layer: no full-corpus decrypt, pluggable vector store

**Goal:** startup cost independent of corpus size, and no corpus plaintext held in memory.

### Scope change: the persistent sparse index moved here from Phase 3
The old BM25 was built at startup from decrypted text. Removing the startup decrypt (this
phase's goal) therefore needs a sparse index that doesn't come from plaintext, so Phase 3 item 1
(`bm25s`, partitioned by `(department, clearance)`, HMAC-hashed terms) is implemented here.
The configurable dense/sparse candidate pools and concurrent dense ∥ sparse search (Phase 3
item 2) also came along, because the rewritten retriever needed them anyway. Phase 3 keeps the
reranker, context token budget and embedding-model docs.

### Added
- `securerag/retrieval/sparse.py`
  - Tokenizer: lowercase alphanumeric terms, with stopwords and 1-char tokens removed.
  - `TermHasher`: `HMAC-SHA256(sparse_key, term)[:8 bytes]`, i.e. 16 hex chars as in the plan.
    `sparse_key` comes from HKDF over the AES key with its own label (`securerag/sparse-terms/v1`).
  - `build_partition`: builds an index from hashed terms, writes it to a temp dir, then swaps it in.
  - `SparseRetriever`: loads partitions lazily, reloads one when its version changes, and only
    queries the partitions a role's RBAC filter allows. Per-partition IDF is deliberate and
    commented in the code.
  - `partitions_for_filter` fails closed: a filter it doesn't understand gives no partitions.
- `securerag/retrieval/qdrant_store.py`: `QdrantVectorStore` for server (`QDRANT_URL`), local
  (`QDRANT_PATH`) or `:memory:` mode.
  - Keyword payload indexes on `department`, `clearance` and `doc_id`.
  - 32-hex chunk IDs map 1:1 to UUID point IDs.
  - Chroma-style filters are translated to Qdrant `Filter`s; unknown operators raise.
  - Store info lives in a tiny `__meta` collection.
  - Chosen with `VECTOR_BACKEND=qdrant`; the package is an optional extra (`pip install .[qdrant]`).
- `securerag/retrieval/store.py`: rewritten around `open_serving_stack(settings, encryptor)`, which:
  - refuses an empty store (`StoreNotReadyError`);
  - refuses a different `embed_model` (Phase 3 item 5, done here);
  - refuses a different key id (`StoreKeyError`);
  - decrypts a random sample of 5 chunks to prove the key, raising `StoreKeyError` on
    `DecryptionError`;
  - then builds the retriever.
  **There is no automatic rebuild anywhere:** `build_store` / `load_store` are gone, and only
  `scripts/ingest.py --full-rebuild` re-encrypts.
- `tests/conftest.py`: an offline stack built through the real ingestion pipeline (real Chroma,
  real sparse index, real AES-GCM) with a bag-of-words `FakeEncoder`.
- `tests/test_storage.py` (20 tests):
  - wrong key fails fast with the store untouched;
  - tampered/undecryptable payload fails;
  - empty store fails;
  - model mismatch fails;
  - no plaintext reachable from retriever, sparse retriever or engine after startup and queries;
  - no plaintext vocabulary in the on-disk sparse index;
  - Chroma and Qdrant `:memory:` both pass the same RBAC/answer tests, 4 roles × 2 backends;
  - dense pre-filter works per backend;
  - a spy shows a guest query only opens `general.public`;
  - HMAC BM25 gives the same top-3 IDs **and scores** as plaintext bm25s;
  - incremental ingest rebuilds only the touched partition;
  - deleting a partition's last doc removes that partition.

### Changed
- `Chunk` is now `(id, metadata, score)`, with no plaintext and no ciphertext.
  `HybridRetriever(store, encoder, sparse, ...)` returns IDs + metadata. It fetches metadata in
  windows and applies `matches_filter` (post-fusion allow-list, fails closed on unknown operators)
  before returning.
- `SecureRAG(store, retriever, encryptor, audit_path)`: `decrypt_sanitize` fetches ciphertext by
  ID for the authorized top-k only and decrypts it inside the request.
- Ingestion stores each chunk's partition and hashed terms in the state DB and rebuilds only the
  partitions touched by a run: new, changed, relabelled (old and new partition), rejected or
  deleted docs. It also rebuilds partitions whose index is missing on disk.
  `--full-rebuild` wipes the sparse dir.
- `state.py`: `chunks.partition` and `chunks.terms` columns (auto-migrated), `mark_indexed` keeps
  the claimed rows and drops stale ones.
- Settings: `VECTOR_BACKEND`, `QDRANT_URL`, `QDRANT_PATH`, `QDRANT_API_KEY`, and `SPARSE_DIR`
  (default `<store>_sparse`).
- The API lifespan, Streamlit app, evaluation runner and benchmark adapter use `open_serving_stack`.
- Deps: `rank-bm25` → `bm25s`; `qdrant-client` (optional extra, also in requirements.txt).

### Tests updated on purpose
- `tests/test_api.py`, `tests/test_pipeline.py`, `tests/test_retrieval.py` built `Chunk(text=...)`
  over a `MagicMock` collection, and that API no longer exists (the point of this phase). They now
  use the shared real-pipeline fixture, and every original assertion is kept.
  `test_pipeline.py` gained a no-plaintext-on-chunks check. `test_retrieval.py` gained per-role
  "never returns unauthorized chunks", fail-closed filter and partition-mapping tests.
- `tests/test_evaluation.py` fixture: `build_store` → `run_ingestion(full_rebuild=True)` +
  `open_serving_stack`. Retrieval-quality thresholds on `data/sample` (Recall@1 ≥ 0.8, Recall@5 ≥ 0.95,
  MRR ≥ 0.85) still pass with the real model, new chunker and new sparse index.

### Known limitation (for Phase 5)
- A running API holds an open Chroma collection handle. `ingest --full-rebuild` from another
  process deletes and recreates the collection, so the server must restart afterwards.
  Incremental ingests are fine: upsert/delete in place, and sparse partitions reload by version.

### Dense-path performance: what was tried
- Profiling at 2000 docs showed the dense query dominating retrieval. Chroma evaluates `where`
  by scanning matching metadata: the exec role's `$and`-of-`$in` filter took **53 ms**, against
  **1 ms** unfiltered.
- **Tried one Chroma collection per partition** (mirroring the sparse index) and **dropped it.**
  With ~15 persistent collections open in one process, chromadb 1.5.9 deterministically broke
  queries on some collection with `Error creating hnsw segment reader: Nothing found on disk`,
  even after only `count()` calls. It reproduced in a standalone script, and neither retries nor
  fresh handles recovered it.
- **Kept:** a single collection. Each chunk gets a `partition` metadata field, the RBAC filter is
  sent as one `partition $in [...]` condition, and a filter that allows every partition (exec) is
  skipped because it's a no-op. Results are still re-checked against the original filter in the
  store, then by the retriever's post-fusion allow-list, then by the graph's `authorize` node.
  Exec retrieval at 2000 docs went from 55 ms to 19 ms.
- Chroma's filtered search is still O(matching rows). For large corpora, use the Qdrant backend
  (payload-indexed filtered HNSW). Phase 6 measures both.
- Tests use an in-memory Chroma client with a unique collection prefix per test. A pytest session
  opening hundreds of `PersistentClient` stores ran into Windows' 512 C-runtime stream limit
  (`numpy ... _fdopen failed`), and `SharedSystemClient.clear_system_cache()` between tests
  triggers the same segment corruption. The real on-disk path is still covered by
  `tests/test_evaluation.py` and the benchmark.

### Results (`reports/scale/phase2_docs_*.md`)
| Metric | 500 docs | 1000 docs | 2000 docs |
|---|---|---|---|
| Engine startup, total (s) | 13.33 | 13.47 | 13.42 |
| - of which embedding model load (s) | 9.61 | – | 9.76 |
| - of which store open + key check + retriever (s) | **1.10** | – | **1.10** |
| Serve peak RSS (MB) | 1,028 | 1,036 | 1,042 |
| Query p95 (ms) | 25.8 | 28.7 | 40.8 |
| Retrieval p95 (ms) | 20.3 | 23.3 | 35.3 |
| Canary leaks | 0 | 0 | 0 |

**Acceptance check:** startup no longer depends on corpus size. The store-dependent part is
1.10 s at both 500 and 2000 docs, and the rest is the fixed model load. Before, startup decrypted
every chunk and serve RSS grew with the corpus. Retrieval p95 grows 1.74× for a 4× corpus
(Phase 3's target is ≤ 2×), against 2.9× before this phase.

### Tests
`pytest -q`: 95 passed (67 before, minus the replaced MagicMock-based tests, plus 21 storage tests
and the rewritten pipeline/retrieval/API tests). 3 consecutive full runs were green.

**Commit:** `feat(storage): pluggable vector store, fail-fast key check, ID-only retrieval`

---

## Phase 3 — Scalable hybrid retrieval (remaining items)

Items 1 (partitioned HMAC bm25s), 2 (candidate pools from settings, concurrent dense ∥ sparse,
post-fusion allow-list) and 5's model-mismatch refusal were done in Phase 2, and so were the
tests for partition isolation (spy), HMAC-vs-plaintext BM25 equality, and model mismatch.

### Added
- `securerag/retrieval/rerank.py`: `Reranker` protocol + lazily loaded `CrossEncoderReranker`
  (`RERANK_MODEL`, default `BAAI/bge-reranker-base`).
- A `rerank` graph node between `retrieve` and `authorize`, entered via a conditional edge only
  when a reranker is configured (`RERANK_ENABLED=true`). With reranking on, `retrieve` returns the
  fused top `RERANK_TOP_N` (default 30) and `rerank` scores them and cuts to `top_k`.
  **Security:** the cross-encoder needs plaintext, so the node decrypts and scores only candidates
  that pass `rbac.authorize` for the caller. Anything else passes through undecrypted for the
  `authorize` node to block and count. Plaintext stays local to the scoring call.
- Context token budget in `decrypt_sanitize` (`CONTEXT_TOKEN_BUDGET`, default 3000). Chunks are
  kept in rank order until the budget is spent, and `dropped_for_budget` is reported in state and
  in the API response. The cost comes from the `tokens` metadata recorded at ingest, so dropped
  chunks are **never fetched or decrypted**. Older chunks without the field fall back to a
  ~4 chars/token estimate after decryption. The top-ranked chunk is always kept.
- `EMBED_QUERY_PREFIX` / `EMBED_DOC_PREFIX` settings for instruction-tuned embedders. `config.py`
  documents `BAAI/bge-small-en-v1.5` and `intfloat/e5-small-v2` (with their required prefixes) as
  upgrades. The doc prefix is part of each file's fingerprint, so changing it re-embeds.
- `store.build_engine(settings, encryptor, stack)` builds the pipeline from settings (reranker,
  budget); the API, Streamlit and benchmark use it.
- `tests/test_rerank_budget.py` (7 tests):
  - the node is skipped when disabled;
  - rerank reorders the wider pool and cuts to top_k;
  - rerank never decrypts or scores an unauthorized candidate, even when the retriever is forced
    to leak confidential IDs;
  - the budget drops low-ranked chunks without decrypting them;
  - the top chunk is always kept;
  - query/doc prefixes reach the encoder;
  - the token estimator works.

### Decisions
- Reranker quality and latency are measured in Phase 6 with the ~90 MB
  `cross-encoder/ms-marco-MiniLM-L-6-v2`. The default `bge-reranker-base` is ~1.1 GB and is off
  by default, so I didn't download it just for the benchmark.

### Acceptance
Retrieval p95, 2000 vs 500 docs: 35.3 / 20.3 ms = **1.74×** (target ≤ 2×). Measured in Phase 2
with the same retrieval code; Phase 3 changes only add the optional node and the budget step.

### Tests
`pytest -q`: 102 passed.

**Commit:** `feat(retrieval): optional cross-encoder rerank node, context token budget, embed prefixes`

---

## Phase 4 — Security hardening

### 1. Authentication (the role no longer comes from the request body)
- `securerag/security/auth.py`: `Authenticator` with three modes.
  - `api_key`: `X-API-Key`, keys file stores SHA-256 hashes only.
  - `jwt`: HS256/RS256, algorithm pinned, `exp`+`sub` required, `aud`/`iss` enforced when set,
    configurable role claim, HS256 secret ≥ 32 bytes.
  - `dev`: `X-Dev-Role`, refused at startup unless `ENV=dev`, with a loud warning.
  Misconfiguration (missing keys file, short secret, dev mode in prod) stops startup with
  `AuthConfigError`. **Defaults are `ENV=prod` and `AUTH_MODE=api_key`** (secure by default);
  `.env.example` opts into dev for the local demo.
- `scripts/create_api_key.py`: generates `srag_…` keys, prints them once, stores only the hash.
- `app/api.py`:
  - `QueryRequest` has no `role` and `extra="forbid"`, so a body role is a 422.
  - The role comes from `Depends(get_principal)`, and the principal ID is passed to the audit log.
  - New `GET /api/auth/mode` and `GET /api/auth/me`.
  - `/api/audit` is limited to `AUDIT_READER_ROLES` (default `admin,exec`).
- New `admin` role: may read the audit log (and run admin jobs in Phase 5) but its policy has no
  departments, so it sees no documents (separation of duties).
- React UI: reads `/api/auth/mode`. In dev it keeps the role switcher and sends `X-Dev-Role`.
  Otherwise it shows a credential panel (API key or JWT, kept in `sessionStorage`), locks the role
  picker to the server-reported role, and never sends a role in the body.
- Streamlit: refuses to run unless `ENV=dev`, because its role picker is the identity.

### 2. Encryption: keyring, v2 format, AAD, rotation
- `encryption.py` rewritten.
  - `Keyring` sources: `SECURERAG_KEYRING_FILE`, then `SECURERAG_KEYRING` JSON, then the single
    `SECURERAG_AES_KEY_B64`, then an ephemeral key.
  - Ciphertext format `v2:<key_id>:<b64(nonce||tag||ct)>`. v1 blobs still decrypt, by trying each
    key in the ring; `SECURERAG_ALLOW_V1=false` rejects them after migration.
  - **AAD = `securerag/chunk/v2|chunk_id|department|clearance`** on every chunk payload.
- **The index key is separate from the active key.** HMAC subkeys for chunk IDs, fingerprints and
  sparse terms come from the keyring's `index` key, so rotating the encryption key doesn't change
  IDs or indexes. The store records `index_key_id` (a key fingerprint). For a single-key setup
  this equals the old key ID, so stores from Phases 1–3 still open.
- `securerag/security/rotation.py` + `scripts/rotate_key.py`: batch re-encryption under the
  active key. It resumes via `chunks.key_id` in the state DB, which is updated only after the
  store write. `--all` also upgrades v1 blobs to v2 + AAD.
- `VectorStore.update_payloads` was added (Chroma + Qdrant). Chroma's `update()` with documents
  but no embeddings silently re-embeds with its *default embedding function*, and
  `embedding_function=None` doesn't stop that in chromadb 1.x. So the stored vectors are fetched
  and passed back explicitly.

### 3. Injection scanning at ingest
- `securerag/security/injection.py`: `InjectionDetector` runs the regex heuristics, OR-ed with an
  optional HF text classifier (`INJECTION_CLASSIFIER`, label and threshold configurable) that runs
  at ingest only.
- Query time uses the stored `injection_flagged` metadata. It only re-scans chunks that predate
  the flag. `build_safe_context_block(chunks, flags)` still wraps flagged chunks.

### 4. Audit log
- `audit.py` rewritten.
  - Hash chain: `seq`, `prev_hash`, `entry_hash = sha256(canonical JSON)`.
  - Cross-process `filelock` for writes, plus fsync.
  - Size-based rotation to `audit.NNNNN.jsonl`; the chain continues across files.
  - Tail reads from the end of the file.
  - `principal_id`, `reranked` and `dropped_for_budget` are recorded.
  - A pre-chain log from older versions is set aside as `audit.legacy-<ts>.jsonl`.
- `scripts/verify_audit.py`: prints the first broken `seq`/file/line and exits with 1.

### 5. API hardening
- CORS comes only from `CORS_ORIGINS` (empty = same-origin), with no credentials, explicit
  methods and explicit headers.
- A middleware assigns or validates `X-Request-ID` and turns any unhandled exception into
  `{"detail": "Internal server error", "request_id": …}`; the traceback goes to the server log only.
- Body limit: `MAX_REQUEST_BYTES` (413); bodies without `Content-Length` get 411.
- Per-principal rate limiting (`RATE_LIMIT`, 429). **Deviation:** the plan names slowapi. I used
  `limits` directly (the library under slowapi) as a FastAPI dependency keyed by the
  *authenticated principal*, which slowapi's IP-keyed decorators don't do cleanly. Multi-worker
  deployments can use `RATE_LIMIT_STORAGE=redis://…`.

### 6. SECURITY.md
Rewritten: invariants, assets, a 14-row threat table (threat → control → residual risk), and
known limitations, including embedding inversion and HMAC term-frequency analysis.

### 7. Tests
- `tests/test_api.py` (28):
  - body role → 422, and spoofing via header/body is impossible with API keys;
  - wrong, missing or invalid keys → 401;
  - dev mode refused in prod; missing keys file refused;
  - JWT valid, plus 8 rejection cases: expired, forged signature, wrong audience, unknown role,
    no role, no exp, `alg=none`, garbage;
  - short HS256 secret refused;
  - generic 500s with no detail leak; 413 on oversized bodies;
  - per-principal 429; CORS denies foreign origins;
  - audit readable only by admin/exec; admin sees no documents; `/auth/me`.
- `tests/test_hardening.py` (16):
  - v2 format + AAD binding; v1 compatibility and the kill switch;
  - keyring decrypts old keys, encrypts with the active one, and keeps the index subkeys stable;
  - bad or unknown key IDs;
  - **a relabelled chunk fails authentication instead of leaking to guest**; swapped ciphertexts fail;
  - rotation round trip (IDs and vectors unchanged, state updated, resumable, the rotated store
    answers queries);
  - `--all` v1 upgrade;
  - audit chain catches edited and deleted lines, survives rotation, stays valid with concurrent
    writers, records the principal and reads from the end;
  - the `verify_audit.py` exit codes;
  - query time uses the stored injection flag (spy shows no re-scan); the classifier catches what
    the regex misses.
- Updated on purpose: `test_ingestion.py` now decrypts with AAD and asserts that decrypting
  without it fails. `test_rerank_budget.py`'s encryptor wrapper forwards `aad`.

### Manual end-to-end check
Real model, `data/sample`, uvicorn on :8765.
- **Dev mode:**
  - guest gets no margin, finance_lead gets 34.2%;
  - body `role` → 422; employee reading audit → 403;
  - `verify_audit.py` on the live log reports ok;
  - in the React UI, the finance query answered with the injection quarantined.
- **`ENV=prod AUTH_MODE=api_key`:**
  - no key → 401, and `X-Dev-Role: exec` → 401;
  - a valid finance key via curl → `fiona / finance_lead` (the dev header is ignored);
  - the UI shows the sign-in panel and a locked role picker.
  - I didn't type the key into the browser field myself; that's for you to try.

### Results
`reports/scale/phase4_docs_2000.md`:
- 0 canary leaks.
- Retrieval p95 34.4 ms (unchanged).
- Query p50 43.9 ms, against 37.2 ms in Phase 2. The difference is the fsync'd, locked,
  hash-chained audit append on every query.
- Store open + key check 1.16 s.

`pytest -q`: 139 passed.

**Commit:** `feat(security): authenticated roles, keyring with AAD and rotation, hash-chained audit, API hardening`

---

## Phase 5 — Runtime robustness and deployment

(Done before Phase 6, so the final evaluation and README describe the finished system.)

### 1. LLM layer — `securerag/llm/router.py`
- One cached SDK client per provider (`lru_cache`), SDK-level retries off, and a timeout
  (`LLM_TIMEOUT_S`, default 30 s).
- `tenacity` exponential backoff (`LLM_MAX_RETRIES`, default 3), but **only** for retryable failures:
  408/409/425/429/5xx/529, timeouts and connection errors. 400/401/403 go straight to the next
  provider, and a missing API key (`ProviderUnavailable`) is skipped without retrying.
- Ordered fallback chain `LLM_PROVIDER` + `LLM_FALLBACKS` (default `refusal`). `refusal` returns a
  fixed "temporarily unavailable" message; if the whole chain fails the router refuses instead
  of raising.
- **Behaviour change:** the old code silently fell back to the *mock extractive answerer* when a
  key was missing or a call failed, so production would have answered with regex-extracted
  sentences. It now falls back to the configured chain and ends in a refusal. `mock` is still
  available explicitly (tests, benchmarks).
- The graph state and audit log record `llm_provider` (the provider that actually answered),
  `llm_attempts` and `llm_fallbacks`. `call_llm()` stays as a compatibility wrapper.

### 2. Streaming — `POST /api/query/stream` (SSE)
- `SecureRAG.prepare()` runs a second compiled graph (validate → retrieve → [rerank] → authorize →
  decrypt_sanitize), so auth and validation errors are still normal HTTP codes. The response then
  streams `meta` (counts, sources, excerpts), `token` pieces and `done` (provider, attempts,
  latency), or `error` with only the request ID.
- `stream_answer()` writes the audit entry **after** the stream completes (`streamed: true`), so
  the answer hash covers the full text. Pieces come from `LLMRouter.stream`, which splits the
  routed result, so retries and fallbacks behave exactly as in the non-streaming path.
  Provider-native token streaming is a possible follow-up.

### 3. Health
- `/api/health`: liveness.
- `/api/ready`: returns 503 until startup has verified the store and key, warmed the embedder
  (one encode) and loaded every sparse partition (`SparseRetriever.warm()`), and while the store
  is empty.

### 4. Logging — `securerag/logging_config.py`
JSON lines (`ts`, `level`, `logger`, `request_id`, `msg`, extras, `exc`). The request ID travels
in a `ContextVar` set by the middleware (and inside SSE generators), so every line from the
retriever, router or audit writer carries it. Each request gets one access log line.
`LOG_FORMAT=text` is available for local work.

### 5. Admin ingestion — `securerag/ingestion/jobs.py`
- `POST /api/admin/ingest` (admin role only, `{"full_rebuild": bool}`, no client-supplied paths,
  so it can't be pointed at arbitrary server folders) returns 202 with a `job_id`. Starting a
  second job while one runs returns 409, and each start is written to the audit chain.
- `GET /api/admin/ingest/{job_id}` reads progress from the state DB `runs` table, which the
  pipeline updates after every flushed batch.
- The job runs against the **serving store object and encoder**, so new chunks are queryable at
  once with no restart; the test proves a new document is retrievable right after the job.

### 6. Docker
- `Dockerfile`:
  - three stages: Vite build, then Python venv with CPU-only torch and the embedding model baked
    in, then a slim runtime;
  - non-root uid 10001, `HF_HUB_OFFLINE=1`, a `/data` volume and a healthcheck;
  - secure defaults `ENV=prod AUTH_MODE=api_key`.
- `docker-compose.yml`:
  - `qdrant` (not published on the host), `api` (healthcheck on `/api/ready`) and a one-shot
    `ingest` profile;
  - a shared `/data` volume for the state DB, sparse indexes, audit log and API keys.
- **Not built locally:** Docker isn't installed on this machine. Both files were
  syntax-checked (YAML parsed) and follow standard patterns, but the first `docker compose build`
  still needs a real run.

### 7. CI — `.github/workflows/ci.yml`
- `lint`: ruff + mypy on `securerag/`.
- `test`: CPU torch, pytest, then `benchmark_scale.py --docs 300 --queries-per-role 50
  --fail-on-leak`, uploading the report as an artifact.
- `frontend`: `npm ci && npm run build`.
- The same benchmark command passed locally (0 leaks).
- Added `[tool.ruff]` (E, F, W, B, I, UP at 120 columns) and `[tool.mypy]` to `pyproject.toml`.
  A user-level ruff config on this machine enables ~100 extra rules, so pinning the project config
  keeps CI and local runs identical. Cleanup: 98 autofixes (imports, `UP` modernisations), a few
  wrapped lines, and `# noqa: E501` only on long data strings. mypy found 11 real typing gaps:
  `RolePolicy` TypedDict, the HKDF return type, rerank assert, list annotations. Both tools are
  clean now.

### 8. UI consolidation
The React frontend is the primary UI.
- **Kept** `app/streamlit_app.py` as a dev-only demo (it refuses to run unless `ENV=dev`, see Phase 4).
- **Removed** the root `streamlit_app.py` shim; the README now says `streamlit run app/streamlit_app.py`.
- **Removed** `frontend/src/app.js` + `style.css`, a leftover vanilla-JS UI that `index.html` no
  longer loads.

I made these calls without asking, as you instructed. Restore with
`git checkout HEAD~1 -- streamlit_app.py frontend/src/app.js frontend/src/style.css` if you want them back.

### Tests
- `tests/test_runtime.py` (21):
  - router: retry then success; fallback after exhausted retries; no retry on 401 or missing key;
    refusal when everything fails; stream equals generate; chain parsing; `is_retryable` matrix;
  - graph records provider, attempts and fallbacks in state and audit;
  - SSE: event order, tokens equal the non-stream answer, audit written after the stream with a
    valid chain; body role → 422 on the stream; guest can't see the margin;
  - `/api/ready` 200/503;
  - admin job lifecycle: 403 for exec, 422 for a `data_dir` field, completion, 404 for a bad ID,
    new doc queryable, audit entry; 409 for a concurrent job;
  - JSON logs carry the request ID.
- `pytest -q`: 160 passed. `ruff check`: clean. `mypy securerag`: clean.

**Commit:** `feat(runtime): LLM router with retries/fallbacks, SSE, readiness, JSON logs, admin ingest, Docker, CI`

---

## Phase 6 — Evaluation at real scale

### Dense RBAC filtering on Chroma, round 2 (found by the 10k/50k runs)
- With strict filtering, retrieval p95 for employee/finance_lead reached ~100 ms at 31k chunks, while
  exec (no filter) stayed at 27 ms. The first 50k run served with an early over-fetch variant that
  read metadata for every candidate, and its employee p95 was ~550 ms. That run was superseded, and
  strict filtering was never measured at 50k.
- Profiling at 31k chunks: HNSW itself is cheap (top-2000 IDs in 9 ms). The cost is **metadata
  reads, ~0.09 ms per row**, which both Chroma's `where` filter and a naive over-fetch pay. A first
  over-fetch that read metadata for 800-2000 candidates made guest *slower* (50 → 138 ms), so I
  dropped it.
- **Partition-prefixed chunk IDs** (`ID_SCHEME = "pp1"`): chunk ID = `sha256(partition)[:4]` +
  28-hex keyed hash (still 32 hex, so the Qdrant UUID mapping is unchanged).
  - Over-fetch now asks HNSW for IDs + distances only, keeps IDs whose prefix is allowed, and reads
    metadata for the final `n` only, re-checking it with `metadata_matches_filter`. A mismatch
    between ID and metadata falls back to the exact filter.
  - The prefix restates the partition, which is already plaintext metadata, so no new information
    is exposed.
  - Relabelling a document now changes its chunk IDs; the pipeline's stale-ID cleanup already
    handles that.
  - `ID_SCHEME` is part of each file fingerprint and the store identity, so an older store is
    re-ingested rather than served with mismatched IDs.
- **Adaptive strategy:** the pipeline records per-partition chunk counts in the store info at the
  end of each run. A role seeing < 15% of the corpus (guest) uses the exact filter, which is cheap
  because few rows match. Larger roles over-fetch with k = 4n/f, then 16n/f, then 8000 (f = allowed
  fraction), and fall back to exact. `CHROMA_PREFILTER=strict` disables over-fetch.
- **Side effect found by the relabel-attack test:** with prefixed IDs, a chunk whose metadata was
  relabelled in the vector DB is dropped by the ID check before decryption. The strict path still
  relies on AAD, and that test now covers both paths.
- The strict-filter results for 1k/10k are kept as `reports/scale/phase6strict_docs_*` for comparison.
- Remaining limit: guest-style queries are bounded by Chroma's metadata scan (~47 ms p95 at 31k
  chunks). Qdrant with payload indexes is the backend for that regime. It is implemented and tested
  in `:memory:` mode, but I couldn't benchmark it as a server here because Docker isn't available.

### Final results

**Scale**, from `reports/scale/phase6_docs_{1000,10000,50000}.md`. The last column is retrieval p95
per role (ms): exec / employee / finance_lead / guest.

| Docs | Chunks | Chunks/s | Ingest RSS | Serve RSS | Store open + key check | Leaks | Escalations | Retrieval p95 |
|---|---|---|---|---|---|---|---|---|
| 1,000 | 3,101 | 84 | 1,074 MB | 1,069 MB | 1.14 s | 0 | 0/25 | 21 / 25 / 21 / 19 |
| 10,000 | 31,429 | 120 | 1,165 MB | 1,180 MB | 1.36 s | 0 | 0/25 | 28 / 36 / 30 / 47 |
| 50,000 | 155,860 | 114 | 1,412 MB | 1,561 MB | 2.04 s | 0 | 0/25 | 49 / 99 / 57 / 156 |

- **Strict vs adaptive at 10k** (`phase6strict_docs_10000` vs `phase6_docs_10000`): employee
  102 → 36 ms, finance_lead 101 → 30 ms, guest 50 → 47 ms, exec 29 → 28 ms.
- **Startup:** the store-dependent part grows 1.1 → 2.0 s over 50× more chunks (count, the sampled
  key check, sparse manifest reads). The ~9.4 s embedding-model load is constant. The previous
  design decrypted every chunk at startup.
- **Memory is not perfectly flat.** Ingest peak RSS goes 1.07 → 1.41 GB from 3k to 156k chunks.
  The main contributors are the per-partition bm25s rebuild at the end of a run (it holds one
  partition's hashed-term matrix, ~10k chunks at 50k docs) and Chroma's own caches. Serve RSS grows
  1.07 → 1.56 GB, mostly the loaded sparse partitions plus Chroma's HNSW index in memory. Both grow
  far slower than the corpus (50×).
- **Injection detection** (regex only, per chunk): 56-58% with 0 false positives. Half the planted
  payloads are paraphrased on purpose (`EVASIVE_INJECTION_PAYLOADS`), so this number isn't
  self-fulfilling.

**BEIR** (`reports/beir/*.md`). nDCG@10 for exec (whole corpus):

| Dataset | Dense | Sparse | Hybrid | Hybrid + rerank | Published MiniLM-L6 / BM25 |
|---|---|---|---|---|---|
| SciFact | 0.657 | 0.620 | 0.682 | 0.699 | ~0.645 / ~0.665 |
| FiQA | 0.375 | 0.223 | 0.342 | 0.376 | ~0.369 / ~0.236 |

- Dense matches published MiniLM numbers, so the encrypted pipeline costs no quality. Sparse is a
  few points under Anserini BM25 because the tokenizer has no stemming.
- On FiQA the weak sparse side pulls RRF below dense-only, and the cross-encoder recovers it.
  Possible follow-ups: stemming in the sparse tokenizer, and weighted RRF.
- 0 leaks in all 32 role × mode × dataset combinations.
- The reranker is `cross-encoder/ms-marco-MiniLM-L-6-v2` (~90 MB). The configured default
  `BAAI/bge-reranker-base` wasn't downloaded.

### Downloads made during this phase
- BEIR SciFact (2.8 MB) and FiQA (~17 MB) from `public.ukp.informatik.tu-darmstadt.de`, cached in
  `data/beir/` (git-ignored). The certifi CA bundle is used because this Python lacked an
  intermediate certificate; TLS verification stays on.
- `cross-encoder/ms-marco-MiniLM-L-6-v2` from Hugging Face.

### Added
- `scripts/eval_beir.py`, `scripts/plot_scale.py`.
- `scripts/evaluate.py` now builds `reports/evaluation.{md,json}` from the runs. The old 5-doc
  suite is behind `--fixture` and writes to `reports/fixture/`.
- Benchmark: per-chunk injection detection vs ground truth, and a role-spoofing suite over the
  real API in api_key mode.
- Generator: paraphrased injection payloads.
- Store: partition-prefixed chunk IDs, adaptive dense pre-filter, partition counts in the store
  info, and `CHROMA_PREFILTER` setting.
- Docs: README rewritten with real numbers; `docs/resume_bullets.md` and `docs/interview_notes.md`
  updated.

### Tests
`pytest -q`: 163 passed. ruff and mypy clean.

**Commit:** `feat(eval): BEIR under RBAC, 1k/10k/50k security+scale runs, adaptive dense pre-filter`

---

# AWS plan (`AWS_PLAN.md`)

Scope: validate the Docker image and the Qdrant server backend (on GitHub Actions, $0), make the
backend Lambda-ready, write the CloudFormation stack and deploy workflow, and prepare the Amplify
frontend. Rules followed throughout:
- **I create no AWS resources.** I only write templates, workflows and scripts for you to run.
- Tests never touch real AWS.
- Nothing is pushed unless you ask.

Where the plan asks for your choice, I made the call myself, as you instructed earlier, and
recorded the reasoning below so it's easy to flip.

## Prompt 1 — Docker + Qdrant benchmark on GitHub Actions

### Added / changed
- `scripts/benchmark_scale.py`:
  - `--backend chroma|qdrant` and `--qdrant-url`. Without a URL, Qdrant runs in local
    (embedded) mode under the work dir, which is enough for a smoke test.
  - Each run gets its own collection (`bench_<label>_<rand>`) and drops it afterwards
    (`_drop_collection`).
  - The report records the backend alongside the runner's CPU/RAM.
  - `--probe` ingests and benchmarks a 300-doc corpus, prints projected wall time for
    1k/10k/50k and the requested size, and refuses the run (exit 3) if it's over `--max-hours`
    (default 4.5). `--probe-only` prints the projection and stops.
  - `--compare <report.json>` adds a per-role retrieval p95 comparison table.
  - `--data-root` lets the benchmark write under the container's `/data` volume.
  - `GIT_COMMIT` is read from the environment when there is no `.git` (inside the image).
  - The workers take a `--work-dir` and build their settings (store, state DB, sparse dir,
    backend, collection) in one place.
- Settings: `COLLECTION_NAME` (`collection_name`), now honoured by both backends.
- `scripts/smoke_api.py`: end-to-end RBAC smoke test against any running API, using only the
  standard library plus `securerag.security.rbac`, so it runs on a bare runner or against Lambda
  later. For each role's key it checks:
  - `/api/auth/me` reports the key's own role;
  - every returned excerpt passes `authorize(role, …)`;
  - `X-Dev-Role: exec` doesn't change the role;
  - a body `role` gets 422;
  - a request with no key gets 401.
  Exit codes: 0 ok, 1 violation, 2 never ready.
- `tests/test_smoke_api.py` (3): the smoke script really reports leaks, escalations and an
  accepted body role.
- `.github/workflows/docker-bench.yml` (`workflow_dispatch`: `docs` default 10000, `backend` default
  qdrant, `max_hours` 4.5). Steps:
  1. Free disk (dotnet, android, ghc, CodeQL, boost, swift), with `df -h` after each heavy step.
  2. Write `.env` with a throwaway, masked AES key and `ENV=prod AUTH_MODE=api_key`.
  3. `docker compose build`.
  4. Start Qdrant and wait on `/readyz`.
  5. Smoke test: `compose run ingest`, one API key per role (masked), `compose up api`, then
     `smoke_api.py`.
  6. Run the probe and the benchmark *inside the API image* against `http://qdrant:6333`, with
     `--fail-on-leak` and `--compare` against the local `phase6_docs_<n>.json` when it exists.
  7. Upload `out/` as an artifact (no commits from CI), then `compose down -v`.
  The job timeout is 350 min.

### Local verification (no Docker here)
- Qdrant embedded mode at 300 docs: 0 canary leaks, 0/25 escalations, and the collection is
  dropped afterwards. Retrieval is slow in embedded mode (brute-force Python), so these numbers
  mean nothing for performance; the server run is what counts. On this machine the probe
  projects 1k 0.04 h, 10k 0.25 h and 50k 1.18 h (GitHub's CPU-only runner will be several times
  slower).
- `smoke_api.py` against a local uvicorn in `api_key` mode, one key per role (5 roles × 5 queries):
  no problems.
- `pytest` 166 passed; ruff and mypy clean.

### Still pending (needs you)
Run **Actions → Docker + Qdrant benchmark** with `docs=10000`, then `docs=50000` if the 10k probe
projects it under 4.5 h. Download the `gha_qdrant_docs_*` artifacts into `reports/scale/` and
commit them. Until that job passes, the Phase 5 note that the Dockerfile and compose are
"not built locally" still applies.

## Prompt 2 — Lambda-ready backend

### Decision: demo corpus is built in CI, option (a) (the plan asked for your choice; I picked (a))
| | (a) Ingest at image build, key via `docker build --secret` | (b) Plaintext docs in image, ingest into /tmp on cold start |
|---|---|---|
| Cold start | Copy ~60 MB of index to /tmp (~1 s) plus the usual ~15-20 s of torch/model load | Embeds ~3.7k chunks on Lambda CPU (~2 vCPU at 3 GB): minutes, which blows the 60 s timeout on every cold start |
| Plaintext in image | None: ciphertext, vectors and HMAC terms only; the corpus stays in the discarded build stage | The whole demo corpus |
| Key handling | The key exists only during one `RUN` (BuildKit secret mount), never in a layer; CI fetches it from SSM | The key is only fetched at runtime |
| Cost | Rotating the key means rebuilding the image | No rebuild on rotation |

(b) doesn't survive the cold-start budget and ships plaintext, so I chose **(a)**. To switch,
replace the `demo-index` stage with a copy of the corpus and call `run_ingestion` from
`prepare_lambda_environment`.

### Added / changed
- `Dockerfile` restructured: `frontend` → `builder` → `app-base` → `demo-index` → `lambda`, with
  `runtime` kept last so it stays the default target used by compose.
  - `demo-index` generates a 1k-doc synthetic corpus (seed 7), merges `data/sample` into it,
    ingests with the AES key from the `aes_key` build secret, and asserts 0 rejected/failed files.
  - `lambda` adds Lambda Web Adapter **0.8.4** (pinned, copied from `public.ecr.aws/awsguru`),
    copies only `/opt/demo/index`, and sets:
    - `AWS_LWA_READINESS_CHECK_PATH=/api/health`, port 8000;
    - **`AWS_LWA_ASYNC_INIT=true`**, because on-demand init times out at 10 s and torch plus model
      load takes longer. Async init stops Lambda from throwing away and restarting a slow init
      (per the aws-serverless skill notes);
    - `LAMBDA_MODE=1`, `HOME` and caches under `/tmp`, reranker off, `LLM_FALLBACKS=refusal`.
  - CPU-only torch as before. `runtime` no longer chowns `/app` (it's read-only for the app user
    anyway); `/data` is still owned by `app`.
- `securerag/runtime/lambda_mode.py`: `is_lambda()` (`LAMBDA_MODE` or `AWS_LAMBDA_FUNCTION_NAME`)
  and `prepare_lambda_environment()`, which:
  - loads secrets from SSM via boto3 (`SSM_AES_KEY_PARAM` and `SSM_API_KEYS_PARAM` required,
    `SSM_GROQ_KEY_PARAM` optional) and fails fast, naming the missing parameters;
  - writes the hashed keys to `/tmp/securerag/api_keys.json` (0600);
  - copies the baked index to `/tmp/securerag/index` and points the store, state DB, sparse dir
    and audit log there;
  - refuses `ENV=dev` / `AUTH_MODE=dev`;
  - sets `AUDIT_STDOUT`.
- `app/api.py`:
  - the lifespan applies Lambda mode on cold start;
  - no `CORSMiddleware` in Lambda mode;
  - admin ingest endpoints return 404;
  - no job manager is created.
- `audit.py`: with `AUDIT_STDOUT` set, each chained entry is also written to stdout as
  `{"audit": …}`, which ends up in CloudWatch Logs.
- Frontend:
  - `VITE_API_BASE` sets the API origin (default `""`, so local and compose keep using relative
    `/api`);
  - on load it polls `/api/ready` every 4 s for up to ~2 min, showing "Waking up the demo backend
    (~20 s)…" instead of an error.
- `.github/workflows/docker-bench.yml`: new step that builds `--target lambda` with a throwaway
  masked key and checks:
  - the adapter and the encrypted index are present;
  - `/build` (the plaintext corpus) is absent;
  - the key is not in `docker history`;
  - the key is not found anywhere in `/opt /app /etc`.
- Deps: `boto3` (runtime); `moto[ssm]` in the `dev` extra and the CI test install.
- `SECURITY.md`: new "AWS Lambda deployment" section:
  - SSM secrets;
  - build-secret index;
  - dev auth refused;
  - **the audit chain is per container**, with stdout as the durable record;
  - per-container rate limits;
  - CORS handled by the Function URL.

### Tests: `tests/test_lambda_mode.py` (14)
- Lambda detection.
- SSM via moto: loading, the optional Groq key, missing required parameters, and a missing
  parameter-name env var.
- `/tmp` relocation of every writable path.
- Dev auth refused (2 cases); missing baked index fails.
- No CORS middleware in Lambda mode, present outside it.
- Admin endpoints return 404.
- **A real cold start:** real model and persistent Chroma. An index is baked with a key, the API
  starts in Lambda mode with moto SSM, then:
  - `/api/ready` returns 200;
  - guest can't see the margin and exec can;
  - an unauthenticated query gets 401 and admin ingest is disabled;
  - audit JSON reaches stdout;
  - nothing is written next to the baked index.

Two test-isolation traps showed up and are fixed:
- `importlib.reload(app.api)` rebinds `state`, so the fixture keeps the original dict object.
- `prepare_lambda_environment` writes to `os.environ`, so those variables are registered with
  monkeypatch to be restored.

`pytest` 180 passed (two consecutive runs); ruff and mypy clean; `vite build` passes with and
without `VITE_API_BASE`.

### Not verifiable here
- The `lambda` image build (no local Docker). The docker-bench workflow now builds and inspects it.
- LWA 0.8.4's async-init behaviour on real Lambda. Check the first cold start's `INIT_REPORT` in
  CloudWatch.

## Prompt 3 — Infrastructure and deploy pipeline

### Added
- `deploy/aws/backend.yaml` (CloudFormation; cfn-lint clean for ap-south-1; every resource tagged
  `project=securerag`; skill conventions: `Description`, `com.aws.cloudformation.Context`
  why/must on each resource, the `aws-cloudformation@3` marker). Resources:
  - **ECR**: scan on push, immutable SHA tags, lifecycle keeps 2 images, `EmptyOnDelete` so
    teardown removes the images. Images can be rebuilt from git, so the repo isn't retained.
  - **Lambda**: image, x86_64, 3008 MB, 60 s, `/tmp` 1024 MB, reserved concurrency 2.
    `LoggingConfig` points at a 7-day log group. The environment holds only SSM parameter names
    (plus `LLM_PROVIDER=groq`, `RATE_LIMIT=20/minute`).
  - **Function URL**: `AuthType NONE`, CORS locked to the `AmplifyOrigin` parameter, methods
    GET/POST, headers authorization/content-type/x-api-key/x-request-id. There are **two**
    `AWS::Lambda::Permission`s: `InvokeFunctionUrl` (public) and `InvokeFunction` with
    `InvokedViaFunctionUrl: true`. A URL call needs both; the aws-serverless skill notes that
    granting only the first returns 403.
  - **Execution role**: logs on its own log group; `ssm:GetParameter(s)` on
    `parameter/securerag/*`; `kms:Decrypt` with `kms:ViaService = ssm.<region>`; trust limited by
    `aws:SourceAccount`.
  - **Deploy role (GitHub OIDC)**: trust requires `aud=sts.amazonaws.com` and
    `sub=repo:<GitHubRepo>:ref:refs/heads/<GitHubBranch>`. It may push to this repo only
    (`ecr:GetAuthorizationToken` is the one `*` resource, because it can't be scoped) and call
    `UpdateFunctionCode`, `GetFunction` and `GetFunctionConfiguration` on this function only.
    **Deviation from the plan:** the plan listed only `UpdateFunctionCode`. `GetFunction` is
    needed to read the current image for rollback, and `GetFunctionConfiguration` for
    `aws lambda wait function-updated-v2`. It can also read the single AES-key parameter needed
    for the build-time encrypted index (the option (a) choice from Prompt 2).
  - **Two-pass deploy** through the `HasImage` condition: a container function can't be created
    before an image exists. Pass 1 creates ECR and the roles; pass 2 with `ImageUri` creates the
    function, URL and log group.
  - Outputs: `FunctionUrl`, `FunctionName`, `EcrRepositoryUri`, `DeployRoleArn`.
- `.github/workflows/deploy-backend.yml` (`workflow_dispatch`, and pushes to main touching
  `securerag/`, `app/`, `Dockerfile` or `requirements.txt`). Steps:
  1. OIDC login, then fetch the AES key from SSM (umask 077, masked).
  2. `buildx --target lambda --provenance=false --sbom=false`, because Lambda rejects image
     indexes. The key goes in as a secret and is deleted after the build.
  3. Push `:<sha>`.
  4. Record the previous image URI, `update-function-code`, and wait.
  5. `smoke_api.py` against the Function URL with every demo key.
  6. **On failure, roll back to the previous image.**
  The job is skipped while `vars.AWS_DEPLOY_ROLE_ARN` is unset, so pushes before setup don't fail.
- `scripts/create_demo_keys.py`: guest/employee/exec keys. The plaintext JSON goes to stdout
  once (for the GitHub secret `DEMO_KEYS_JSON` and the demo page); the hashed JSON goes to a file
  for SSM. `data/demo_*.json` is git-ignored.
- `deploy/aws/README.md`, with a cost for each step:
  - prerequisites (budget, OIDC provider, demo keys, SSM);
  - the two-pass first deploy, including the manual first image push;
  - Amplify steps (SPA rewrite rule, CSP placeholder, CORS origin update, WAF off);
  - redeploy and rollback;
  - demo-key and AES-key rotation;
  - teardown.
- CI `lint` job now runs `cfn-lint deploy/aws/*.yaml --regions ap-south-1`.
- `tests/test_deploy_assets.py` (4):
  - demo keys authenticate as their roles and only hashes are stored;
  - the template caps: concurrency ≤ 2, 7-day logs, 2 images, no NAT/EC2/provisioned versions;
  - no secret values in the function environment, OIDC trust scoped to a branch, CORS never `*`;
  - the only `*` resource is `ecr:GetAuthorizationToken`, and there are no wildcard actions.

cfn-lint 1.53.3 went into the project venv (the plan asks for cfn-lint in CI).

### Needs you (AWS account work)
Run README §0-§2 in order. Nothing has been created in AWS.

## Prompt 4 — Amplify frontend

### Added
- `amplify.yml` at the repo root. Monorepo `appRoot: frontend`; `npm ci`, then `npm run build`,
  then `dist/**` as the artifact, with `node_modules` cached. `customHeaders` for every path:
  - CSP: `default-src 'self'`, `script-src 'self'`, and `connect-src 'self'
    FUNCTION_URL_PLACEHOLDER` (you replace the placeholder, per the plan). Google Fonts are
    allowed for styles/fonts; `frame-ancestors 'none'`, `base-uri`/`form-action` `'self'`,
    `object-src 'none'`.
  - `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY`, HSTS, `Permissions-Policy`.
  Amplify needs `AMPLIFY_MONOREPO_APP_ROOT=frontend` for an `appRoot` build; the README says so.
- `frontend/src/components/AboutDemo.jsx`, an "About this demo" panel covering:
  - synthetic data only, with planted canaries;
  - the cold-start note (~20 s);
  - links to the source, the benchmark report and SECURITY.md.
  It's open by default on the hosted build (`VITE_API_BASE` set) and collapsed locally. When
  `VITE_DEMO_KEYS_JSON` is set it lists the demo roles with a **Use** button, which signs in with
  that key through the normal credential flow; the server still derives the role. Keys are hidden
  in dev auth mode.
- `deploy/aws/README.md` §2 already had the SPA rewrite rule
  (`</^[^.]+$|\.(?!(css|…)$)([^.]+$)/>` → `/index.html`, 200) and the CORS/CSP steps. The
  environment-variable list is now complete.
- `SECURITY.md`:
  - "Public demo credentials": why showing the keys is acceptable (synthetic data, keys locked to
    roles server-side, reserved concurrency 2, per-key rate limit, the Groq free tier with a
    refusal fallback, no admin key published, rotation) and a rule to never do it for real data;
  - "Frontend headers".

### Verified
- `vite build` passes with and without the hosted variables.
- Served the hosted build locally (`vite preview`) in the in-app browser. The header shows
  "Waking up the demo backend (~20 s)…" while `/api/ready` is unreachable, the About panel is
  open with the three demo roles and Use buttons, and the credential panel shows because the
  auth mode falls back to `api_key` when the backend can't be reached.
- `amplify.yml` parses and the CSP folds to a single header line.

### Needs you
The Amplify console steps in deploy/aws/README.md §2, and the CSP placeholder replacement.

## CI fix — lint job mypy failure (run 36153119442 on daec6a5)
- Cause: the lint job installs only ruff/mypy/pydantic, so PyJWT is missing and mypy (ignore_missing_imports)
  sees `jwt.decode` as untyped. The `# type: ignore[arg-type]` on `securerag/security/auth.py` then became an
  unused ignore, which `warn_unused_ignores` turns into an error. Locally (PyJWT installed) it was needed, so
  the two environments disagreed.
- Fix: `options=cast(Any, options)` instead of the ignore; passes mypy both with and without PyJWT. Verified in a
  clean venv matching the CI lint install (mypy, ruff, cfn-lint all clean) and in the full venv (184 tests).

## Deploy bootstrap without local Docker + credential setup script
- No Docker on the dev machine, so `deploy-backend.yml` gained a bootstrap mode: if the Lambda function doesn't
  exist yet (before stack pass 2) the job stops after pushing the image and prints the ImageUri; the smoke test
  is skipped. Once the function exists the normal update/smoke/rollback path runs.
- `deploy/aws/setup_credentials.ps1`: run by the owner in a terminal. Logs in `gh` (workflow scope) and an AWS
  profile `securerag`, generates a fresh AES key and the demo keys, and writes them to SSM (via temp files, never
  the command line) and the GitHub secret DEMO_KEYS_JSON; the Groq key is read with a hidden prompt. Secrets never
  pass through the assistant.
- `deploy/aws/first_deploy.ps1`: owner-run script for sections 0-1 of deploy/aws/README.md (push, budget,
  OIDC provider, stack pass 1, GitHub variables, image build via the workflow's bootstrap mode, stack pass 2,
  FUNCTION_URL, smoke test). Idempotent. Written as a script because the assistant does not run AWS or push
  commands itself in this setup.

## Fix — deploy role OIDC trust (run 36334297285 failed at AssumeRoleWithWebIdentity)
- The repo uses GitHub's immutable OIDC subject (`use_immutable_subject: true`), so the token's `sub` is
  `repo:Ayush1860@89765953/SecureRag@1355095267:ref:refs/heads/main`, not `repo:Ayush1860/SecureRag:...`.
- The trust policy now accepts exactly those two subjects (new parameter `GitHubRepoIds`, no wildcards); the
  test asserts two exact subjects on the branch. Re-running first_deploy.ps1 updates the role in pass 1.

## Fix — image build hit public.ecr.aws 429 (run 36335911410)
- OIDC now works. The build failed pulling `public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4`:
  `429 Too Many Requests - Data limit exceeded` (anonymous quota shared by GitHub runner IPs).
- The deploy workflow logs in to public.ecr.aws first (`aws ecr-public get-login-password --region us-east-1`).
  The deploy role gains `ecr-public:GetAuthorizationToken` + `sts:GetServiceBearerToken` on "*" (auth-token
  actions, no resource scoping); the wildcard test was updated to allow exactly these. docker-bench.yml has no
  AWS credentials and still pulls anonymously; re-run it if it hits the same 429.

## Fix — demo-index stage: missing /opt/demo (run 36336395593)
- Public ECR login fixed the 429. The build then failed in the demo-index stage: the shell redirect
  `> /opt/demo/ingest_report.json` ran before anything had created /opt/demo ("Directory nonexistent", exit 2).
- Added `RUN mkdir -p /opt/demo/index` before the ingest. Replayed the stage locally with the image's settings
  (ENV=prod, AUTH_MODE=api_key, 1000 synthetic docs seed 7 + data/sample, fresh AES key): 1005 files ->
  3168 chunks, 0 rejected, 0 failed, the same assertion the Dockerfile runs. (No Docker here, so the image itself
  is still first built on Actions.)

## Root-cause review after four failed deploy runs (2026-09-27)
| Run | Failed at | Cause |
|---|---|---|
| 36334297285 | OIDC AssumeRole | repo uses GitHub's immutable `sub`; trust only had owner/repo (fixed bc0f01b) |
| 36335911410 | FROM public.ecr.aws LWA | anonymous 429 data limit (fixed ac898c4) |
| 36336395593 | demo-index RUN | /opt/demo missing (fixed 2daf3f6) |
| 36336984026 | docker push | duplicate build: the push had already started a run (36336931532, **succeeded**, image 2daf3f6 in ECR); the script saw no image yet and dispatched a second run, which hit the immutable tag |

Common cause: the image pipeline had never run end to end (no Docker locally, docker-bench never run), so each
~15 min attempt exposed the next layer. Hardening done in one go instead of one fix per run:
- Workflow skips build/push when ECR already has the commit's tag (idempotent re-runs).
- first_deploy.ps1 waits for a run already started by the push, reuses an image whose build inputs are
  unchanged, and only dispatches when neither exists.
- Pre-empted the next likely failure: new accounts with a concurrency limit of 10 can't reserve any
  (`ReservedConcurrentExecutions` would fail stack pass 2). New parameter `ReservedConcurrency` (0-2, default 2,
  0 = unset); the script reads the account limit and passes 0 below 12.
- Lambda loads the embedding model from a plain directory (`/opt/models/embed` -> baked HF snapshot) in the
  demo-index and lambda stages, so no HF hub/cache code runs on Lambda's read-only filesystem. The embeddings are
  identical to loading by hub id (checked locally). The runtime (compose) image keeps the hub id so existing
  volumes' store identity doesn't change.
- Checked the execution role against the cold-start calls (ssm:GetParameters + kms:Decrypt via SSM): matches.

## Backend live (2026-09-28)
- first_deploy.ps1 completed: push-triggered run 36345928482 built and pushed image 90d79ac (bootstrap mode),
  stack pass 2 created the function, the script's smoke test passed for every demo role.
- Checked from outside: `GET /api/health` -> 200 `{"chunks": 3168, ...}` in 10.8 s (cold start);
  `POST /api/query` without a key -> 401.
- amplify.yml CSP `connect-src` now holds the Function URL origin (placeholder removed).

## Frontend on GitHub Pages instead of Amplify (2026-09-28)
- Amplify "Create app" failed: "You have reached the maximum number of apps in this account" (the account's
  ap-south-1 app limit is taken by another project). The owner chose GitHub Pages.
- `.github/workflows/pages.yml`: builds frontend/ with VITE_API_BASE = vars.FUNCTION_URL, base path /<repo>/,
  deploys with actions/deploy-pages. Demo keys only when vars.PAGES_SHOW_DEMO_KEYS == 'true'.
- `frontend/vite.config.js`: `base` from VITE_BASE_PATH; when CSP_CONNECT_SRC is set, a build-only plugin
  injects the amplify.yml CSP (minus frame-ancestors, which <meta> ignores) and a referrer policy.
- Checked locally: Pages-style build served under /SecureRag/ renders; no CSP violations; the request to the
  Function URL left the page and was refused only by backend CORS (expected from a localhost origin).
- Backend CORS needs `AmplifyOrigin=https://ayush1860.github.io` (owner runs the stack update).
- SECURITY.md notes the Pages limitation (no frame-ancestors / X-Frame-Options; low impact, no session).

## Live site check (2026-09-28) — two issues found
- https://ayush1860.github.io/SecureRag/ loads, shows "System Online (3168 chunks)": CSP, CORS and cold start
  work end to end. Live smoke test (guest/employee/exec, 5 queries each): 0 problems.
- Bug 1 (frontend): authMode started as 'dev', so the first audit fetch carried X-Dev-Role, which the Lambda's
  CORS (correctly) doesn't allow -> preflight failed. authMode now starts null and nothing auth-related is sent
  until /api/auth/mode answers.
- Bug 2 (LLM): every answer was the refusal fallback. CloudWatch: Groq returned 404 NotFoundError for
  llama-3.1-8b-instant (key accepted; model not found or not enabled for the org). The router now logs the
  provider's message (capped at 300 chars; no prompt text or secrets), the model is a stack parameter
  (`GroqModel` -> GROQ_MODEL), and `deploy/aws/check_groq.ps1` lists the models the stored key can use.
- check_groq.ps1 result: the key's org has no llama-3.x models (`model_not_found`); available chat models include
  openai/gpt-oss-20b / -120b and qwen. Default switched to `openai/gpt-oss-20b` (router + template `GroqModel`).
  No max_tokens cap in the router, so gpt-oss's reasoning tokens can't starve the answer.

## Fix — deploy role couldn't update the function image (run 36349211946)
- UpdateFunctionCode by the deploy role: "Lambda does not have permission to access the ECR image". The repo had
  no repository policy; the function was created by an admin whose own ECR rights covered it. The live function
  was unchanged (still 90d79ac).
- Template: ECR `RepositoryPolicyText` lets `lambda.amazonaws.com` BatchGetImage/GetDownloadUrlForLayer, limited
  by aws:SourceAccount and aws:SourceArn = this function. Test added.
- The rollback step ran after the *update* failed and printed "smoke test failed", which was misleading; it now
  runs only when the smoke step itself failed.
