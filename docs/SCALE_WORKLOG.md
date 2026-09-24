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
