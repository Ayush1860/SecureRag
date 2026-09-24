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
