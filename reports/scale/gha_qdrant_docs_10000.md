# Scale benchmark: gha_qdrant_docs_10000

- Generated: 2026-09-28 17:16:04
- Git commit: `b4d78489212c`
- Hardware: 4 CPUs, 15.6 GB RAM, no GPU, Python 3.11.16, Linux-6.17.0-1022-azure-x86_64-with-glibc2.41
- Vector backend: `qdrant`
- Corpus: `../data/bench_root/synthetic/docs_10000` (10000 docs, 4,207,906 words, 3365 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 31429 |
| Ingest wall time (s) | 1070.93 |
| Chunks/sec | 29.3 |
| Process wall time incl. imports (s) | 1072.05 |
| Peak RSS (MB) | 1133.4 |
| Vector store on disk (MB) | None |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 6.96 |
| - embedding model load (s) | 4.969 |
| - store open + key check + retriever (s) | 0.885 |
| Serve process peak RSS (MB) | 817.7 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 67.83 | 71.95 | 73.18 | 61.47 | 65.67 | 0 | 0 |
| employee | 200 | 0 | 72.15 | 77.36 | 78.95 | 65.44 | 69.91 | 0 | 0 |
| finance_lead | 200 | 0 | 71.91 | 75.95 | 76.92 | 65.12 | 69.63 | 0 | 580 |
| exec | 200 | 0 | 77.16 | 80.66 | 85.3 | 70.77 | 72.99 | 0 | 531 |
| **all** | | | 72.11 | 78.7 | 81.28 | 65.49 | 72.14 | 0 | |

## Ingest-time injection detection (per chunk)

| Chunks | TP | FP | FN | Detection rate | FPR |
|---|---|---|---|---|---|
| 31429 | 108 | 0 | 86 | 0.5567 | 0.0 |

## Role-spoofing attempts (AUTH_MODE=api_key)

25 attempts, **0 escalations**, block rate 100.00%.

## Comparison with `phase6_docs_10000` (chroma, 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0)

Retrieval p95 per role (ms). Hardware differs, so compare shapes (how roles scale), not absolute values.

| Role | This run | Reference | Ratio |
|---|---|---|---|
| guest | 65.67 | 47.21 | 1.39x |
| employee | 69.91 | 35.86 | 1.95x |
| finance_lead | 69.63 | 29.74 | 2.34x |
| exec | 72.99 | 27.63 | 2.64x |
