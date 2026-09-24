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
