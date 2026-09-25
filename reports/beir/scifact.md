# BEIR scifact (test) under RBAC

- Docs: 5,183 -> 9,982 encrypted chunks (ingest 129.9 s)
- Test queries: 300; embedder `sentence-transformers/all-MiniLM-L6-v2`; reranker `cross-encoder/ms-marco-MiniLM-L-6-v2`; label seed 42
- Per role, only qrels the role may see count; queries with none visible are skipped.

## guest (22 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.8647 | 0.9545 | 1.0000 | 0.8333 | 16.93 | 0 |
| sparse | 0.8411 | 0.9545 | 1.0000 | 0.8045 | 3.27 | 0 |
| hybrid | 0.8815 | 0.9545 | 1.0000 | 0.8561 | 20.06 | 0 |
| hybrid_rerank | 0.8704 | 0.9545 | 1.0000 | 0.8424 | 811.68 | 0 |

## employee (123 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.7926 | 0.9106 | 0.9512 | 0.7531 | 16.49 | 0 |
| sparse | 0.7577 | 0.8455 | 0.9431 | 0.7307 | 5.17 | 0 |
| hybrid | 0.7924 | 0.9106 | 0.9837 | 0.7564 | 20.37 | 0 |
| hybrid_rerank | 0.8053 | 0.9187 | 0.9837 | 0.7701 | 193.6 | 0 |

## finance_lead (124 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.7792 | 0.9140 | 0.9435 | 0.7413 | 16.46 | 0 |
| sparse | 0.6808 | 0.8051 | 0.8723 | 0.6447 | 4.98 | 0 |
| hybrid | 0.7694 | 0.9180 | 0.9489 | 0.7305 | 20.47 | 0 |
| hybrid_rerank | 0.7949 | 0.9167 | 0.9489 | 0.7580 | 195.73 | 0 |

## exec (300 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.6572 | 0.8111 | 0.8903 | 0.6118 | 11.73 | 0 |
| sparse | 0.6195 | 0.7513 | 0.8461 | 0.5833 | 8.14 | 0 |
| hybrid | 0.6819 | 0.8137 | 0.9203 | 0.6447 | 20.55 | 0 |
| hybrid_rerank | 0.6988 | 0.8412 | 0.9203 | 0.6624 | 195.2 | 0 |
