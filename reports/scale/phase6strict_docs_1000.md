# Scale benchmark: phase6_docs_1000

- Generated: 2026-09-25 04:31:07
- Git commit: `670cbaf`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_1000` (1000 docs, 414,765 words, 302 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 3101 |
| Ingest wall time (s) | 41.97 |
| Chunks/sec | 73.9 |
| Process wall time incl. imports (s) | 42.92 |
| Peak RSS (MB) | 1082.0 |
| Vector store on disk (MB) | 52.62 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.41 |
| - embedding model load (s) | 9.919 |
| - store open + key check + retriever (s) | 1.164 |
| Serve process peak RSS (MB) | 1068.4 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 30.9 | 34.92 | 37.93 | 17.45 | 20.14 | 0 | 0 |
| employee | 200 | 0 | 35.41 | 41.13 | 55.72 | 22.12 | 25.61 | 0 | 0 |
| finance_lead | 200 | 0 | 35.1 | 40.05 | 44.73 | 21.84 | 25.44 | 0 | 535 |
| exec | 200 | 0 | 32.8 | 37.26 | 42.59 | 19.92 | 23.45 | 0 | 557 |
| **all** | | | 34.02 | 39.16 | 44.94 | 21.18 | 24.84 | 0 | |

## Ingest-time injection detection (per chunk)

| Chunks | TP | FP | FN | Detection rate | FPR |
|---|---|---|---|---|---|
| 3101 | 14 | 0 | 10 | 0.5833 | 0.0 |

## Role-spoofing attempts (AUTH_MODE=api_key)

25 attempts, **0 escalations**, block rate 100.00%.
