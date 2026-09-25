# Scale benchmark: phase6_docs_10000

- Generated: 2026-09-25 04:38:12
- Git commit: `670cbaf`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_10000` (10000 docs, 4,207,906 words, 3365 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 31429 |
| Ingest wall time (s) | 327.64 |
| Chunks/sec | 95.9 |
| Process wall time incl. imports (s) | 328.57 |
| Peak RSS (MB) | 1148.0 |
| Vector store on disk (MB) | 481.92 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.18 |
| - embedding model load (s) | 9.586 |
| - store open + key check + retriever (s) | 1.401 |
| Serve process peak RSS (MB) | 1179.8 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 58.65 | 63.98 | 76.48 | 45.59 | 50.22 | 0 | 0 |
| employee | 200 | 0 | 111.24 | 115.32 | 129.56 | 97.94 | 101.73 | 0 | 0 |
| finance_lead | 200 | 0 | 111.11 | 114.37 | 127.1 | 98.01 | 101.34 | 0 | 579 |
| exec | 200 | 0 | 38.17 | 42.23 | 50.11 | 25.49 | 29.11 | 0 | 527 |
| **all** | | | 108.01 | 113.71 | 122.41 | 94.86 | 100.3 | 0 | |

## Ingest-time injection detection (per chunk)

| Chunks | TP | FP | FN | Detection rate | FPR |
|---|---|---|---|---|---|
| 31429 | 108 | 0 | 86 | 0.5567 | 0.0 |

## Role-spoofing attempts (AUTH_MODE=api_key)

25 attempts, **0 escalations**, block rate 100.00%.
