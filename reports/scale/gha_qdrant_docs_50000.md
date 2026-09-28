# Scale benchmark: gha_qdrant_docs_50000

- Generated: 2026-09-28 19:11:06
- Git commit: `b4d78489212c`
- Hardware: 4 CPUs, 15.6 GB RAM, no GPU, Python 3.11.16, Linux-6.17.0-1022-azure-x86_64-with-glibc2.41
- Vector backend: `qdrant`
- Corpus: `../data/bench_root/synthetic/docs_50000` (50000 docs, 21,089,329 words, 16900 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 155860 |
| Ingest wall time (s) | 6273.35 |
| Chunks/sec | 24.8 |
| Process wall time incl. imports (s) | 6274.69 |
| Peak RSS (MB) | 1266.1 |
| Vector store on disk (MB) | None |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 7.01 |
| - embedding model load (s) | 5.269 |
| - store open + key check + retriever (s) | 0.654 |
| Serve process peak RSS (MB) | 976.7 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 70.68 | 76.24 | 77.33 | 64.03 | 69.62 | 0 | 0 |
| employee | 200 | 0 | 73.22 | 78.42 | 80.27 | 66.74 | 71.92 | 0 | 0 |
| finance_lead | 200 | 0 | 70.37 | 75.26 | 76.35 | 63.81 | 68.72 | 0 | 532 |
| exec | 200 | 0 | 74.37 | 78.43 | 83.35 | 67.76 | 71.56 | 0 | 518 |
| **all** | | | 72.26 | 77.85 | 80.27 | 65.67 | 71.33 | 0 | |

## Ingest-time injection detection (per chunk)

| Chunks | TP | FP | FN | Detection rate | FPR |
|---|---|---|---|---|---|
| 155860 | 571 | 0 | 434 | 0.5682 | 0.0 |

## Role-spoofing attempts (AUTH_MODE=api_key)

25 attempts, **0 escalations**, block rate 100.00%.

## Comparison with `phase6_docs_50000` (chroma, 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0)

Retrieval p95 per role (ms). Hardware differs, so compare shapes (how roles scale), not absolute values.

| Role | This run | Reference | Ratio |
|---|---|---|---|
| guest | 69.62 | 155.7 | 0.45x |
| employee | 71.92 | 98.9 | 0.73x |
| finance_lead | 68.72 | 57.28 | 1.20x |
| exec | 71.56 | 48.8 | 1.47x |
