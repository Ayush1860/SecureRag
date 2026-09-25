# BEIR fiqa (test) under RBAC

- Docs: 57,638 -> 74,748 encrypted chunks (ingest 919.1 s)
- Test queries: 648; embedder `sentence-transformers/all-MiniLM-L6-v2`; reranker `cross-encoder/ms-marco-MiniLM-L-6-v2`; label seed 42
- Per role, only qrels the role may see count; queries with none visible are skipped.

## guest (109 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.5164 | 0.6422 | 0.9144 | 0.4852 | 70.36 | 0 |
| sparse | 0.3871 | 0.5138 | 0.6927 | 0.3521 | 2.68 | 0 |
| hybrid | 0.5119 | 0.6927 | 0.8792 | 0.4697 | 72.03 | 0 |
| hybrid_rerank | 0.5197 | 0.6606 | 0.8792 | 0.4834 | 234.98 | 0 |

## employee (404 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.4141 | 0.5220 | 0.7216 | 0.4304 | 32.5 | 0 |
| sparse | 0.2533 | 0.3280 | 0.5078 | 0.2659 | 5.8 | 0 |
| hybrid | 0.3877 | 0.4999 | 0.7044 | 0.4078 | 32.54 | 0 |
| hybrid_rerank | 0.4168 | 0.5123 | 0.7044 | 0.4335 | 200.29 | 0 |

## finance_lead (423 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.4197 | 0.5210 | 0.6929 | 0.4366 | 30.23 | 0 |
| sparse | 0.2435 | 0.3131 | 0.4737 | 0.2609 | 5.32 | 0 |
| hybrid | 0.3619 | 0.4738 | 0.6796 | 0.3770 | 32.13 | 0 |
| hybrid_rerank | 0.4087 | 0.5044 | 0.6796 | 0.4217 | 202.05 | 0 |

## exec (648 evaluable queries)

| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |
|---|---|---|---|---|---|---|
| dense | 0.3750 | 0.4474 | 0.6269 | 0.4470 | 25.65 | 0 |
| sparse | 0.2228 | 0.2850 | 0.4193 | 0.2742 | 9.76 | 0 |
| hybrid | 0.3417 | 0.4172 | 0.6131 | 0.4181 | 26.36 | 0 |
| hybrid_rerank | 0.3759 | 0.4429 | 0.6131 | 0.4489 | 202.16 | 0 |
