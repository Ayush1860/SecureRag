# Scale benchmark: phase6_docs_10000

- Generated: 2026-09-25 05:44:21
- Git commit: `670cbaf`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_10000` (10000 docs, 4,207,906 words, 3365 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 31429 |
| Ingest wall time (s) | 263.03 |
| Chunks/sec | 119.5 |
| Process wall time incl. imports (s) | 263.93 |
| Peak RSS (MB) | 1165.3 |
| Vector store on disk (MB) | 480.94 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 12.83 |
| - embedding model load (s) | 9.286 |
| - store open + key check + retriever (s) | 1.363 |
| Serve process peak RSS (MB) | 1183.1 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 57.33 | 60.53 | 62.17 | 44.33 | 47.21 | 0 | 0 |
| employee | 200 | 0 | 38.18 | 49.09 | 50.49 | 25.29 | 35.86 | 0 | 0 |
| finance_lead | 200 | 0 | 37.29 | 44.86 | 48.21 | 24.67 | 29.74 | 0 | 580 |
| exec | 200 | 0 | 38.68 | 40.81 | 41.98 | 25.91 | 27.63 | 0 | 529 |
| **all** | | | 38.87 | 59.01 | 61.09 | 26.01 | 45.85 | 0 | |

## Ingest-time injection detection (per chunk)

| Chunks | TP | FP | FN | Detection rate | FPR |
|---|---|---|---|---|---|
| 31429 | 108 | 0 | 86 | 0.5567 | 0.0 |

## Role-spoofing attempts (AUTH_MODE=api_key)

25 attempts, **0 escalations**, block rate 100.00%.
