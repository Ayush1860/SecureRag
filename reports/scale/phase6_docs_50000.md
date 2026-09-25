# Scale benchmark: phase6_docs_50000

- Generated: 2026-09-25 06:09:23
- Git commit: `670cbaf`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_50000` (50000 docs, 21,089,329 words, 16900 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 155860 |
| Ingest wall time (s) | 1370.93 |
| Chunks/sec | 113.7 |
| Process wall time incl. imports (s) | 1371.96 |
| Peak RSS (MB) | 1412.0 |
| Vector store on disk (MB) | 2393.57 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.64 |
| - embedding model load (s) | 9.403 |
| - store open + key check + retriever (s) | 2.039 |
| Serve process peak RSS (MB) | 1558.4 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 155.2 | 168.8 | 171.75 | 141.92 | 155.7 | 0 | 0 |
| employee | 200 | 0 | 68.61 | 112.1 | 114.24 | 55.75 | 98.9 | 0 | 0 |
| finance_lead | 200 | 0 | 59.69 | 70.23 | 73.15 | 46.71 | 57.28 | 0 | 526 |
| exec | 200 | 0 | 59.78 | 61.63 | 64.92 | 47.18 | 48.8 | 0 | 513 |
| **all** | | | 61.24 | 164.65 | 170.88 | 48.16 | 150.79 | 0 | |

## Ingest-time injection detection (per chunk)

| Chunks | TP | FP | FN | Detection rate | FPR |
|---|---|---|---|---|---|
| 155860 | 571 | 0 | 434 | 0.5682 | 0.0 |

## Role-spoofing attempts (AUTH_MODE=api_key)

25 attempts, **0 escalations**, block rate 100.00%.
