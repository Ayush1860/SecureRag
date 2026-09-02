# SecureRAG — Security-Hardened Enterprise RAG

A production-style Retrieval-Augmented Generation (RAG) reference application built with defense-in-depth security principles. It combines role-based access control (RBAC), application-level AES-256-GCM document encryption, prompt-injection sanitization, hybrid dense+sparse retrieval, LangGraph workflow orchestration, and structured audit logging.

## Architecture

```text
Browser UI (Cybersecurity SOC Dashboard)
   |
FastAPI (/api/query, /api/roles, /api/audit, /api/health)
   |
LangGraph Orchestration
   |-- 1. validate (identity & RBAC role verification)
   |-- 2. retrieve (ChromaDB dense + BM25 sparse hybrid with metadata filter)
   |-- 3. authorize (post-retrieval verification against cross-boundary leakage)
   |-- 4. decrypt_sanitize (AES-256-GCM decryption + untrusted boundary isolation)
   |-- 5. generate (Grounded LLM: Groq / OpenAI / Anthropic / Gemini / Mock)
   `-- 6. audit (Structured JSONL with SHA-256 hashes & telemetry)
```

## Security Highlights

- **Dual-Stage RBAC**: Authorization is enforced both at the vector/sparse search stage and re-verified post-retrieval before any document is decrypted.
- **AES-256-GCM Document Payloads**: Text content is encrypted at rest using authenticated symmetric encryption with 12-byte random nonces and 16-byte MAC authentication tags.
- **Prompt-Injection Sanitization**: Retrieved content is heuristically inspected for instruction override markers. Flagged chunks are quarantined inside `[UNTRUSTED_DOCUMENT_CONTENT]` delimiters, and system prompts explicitly forbid treating retrieved text as instructions.
- **Deterministic Offline Mode**: Out of the box, `LLM_PROVIDER=mock` executes full grounded responses without requiring external third-party API keys or incurring costs during testing and CI/CD.
- **Structured Audit Logging**: Cryptographically anonymizes queries and answers via SHA-256 hashes while recording timestamps (ISO-8601), user role, retrieved/authorized/blocked counts, prompt-injection flags, and latency.

## Stack

Python 3.11+, Streamlit, FastAPI, LangGraph, ChromaDB, rank-bm25, Sentence Transformers (`all-MiniLM-L6-v2`), PyCryptodome (AES-GCM), Pydantic v2.

## Run Locally

```bash
# 1. Clone and enter directory
git clone https://github.com/your-org/securerag.git
cd securerag

# 2. Set up virtual environment
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Generate a secure 32-byte AES key:
python scripts/generate_key.py
# Paste the generated key into SECURERAG_AES_KEY_B64 in .env

# 5. Ingest and encrypt the corpus
python scripts/ingest.py

# 6. Launch the frontend UI
# Option A: Streamlit interactive frontend
streamlit run streamlit_app.py

# Option B: FastAPI service + static web UI
uvicorn app.api:app --reload --port 8000
```

Open `http://localhost:8000` for the interactive UI or `http://localhost:8000/docs` for the OpenAPI / Swagger documentation.

## Security Roles & Clearances

| Role | Max Clearance | Allowed Departments | Typical Use Case |
| :--- | :--- | :--- | :--- |
| `guest` | `public` (0) | `general` | Anonymous / public inquiries |
| `employee` | `internal` (1) | `general`, `engineering`, `hr` | Standard staff member operations |
| `finance_lead` | `confidential` (2) | `general`, `finance` | Departmental finance head |
| `exec` | `confidential` (2) | `general`, `engineering`, `hr`, `finance`, `exec` | Full organizational oversight |

## Demo Scenarios

The interactive dashboard includes 1-click presets demonstrating security boundaries:

1. **Poisoned Vendor Document (Prompt Injection Quarantine)**:
   - Role: `employee`
   - Query: `"What feedback did we get from LiDAR vendors?"`
   - *Result*: The LiDAR review contains an embedded instruction attack (`"Ignore all previous instructions..."`). SecureRAG flags the threat, isolates it within quarantine delimiters, and the LLM safely summarizes the factual report while neutralizing the injection.
2. **Confidential Memo (Access Denied)**:
   - Role: `guest`
   - Query: `"What is the company's financial situation and gross margin?"`
   - *Result*: All confidential candidate chunks are blocked. Zero unauthorized data is leaked or passed to the model.
3. **Confidential Memo (Authorized Access)**:
   - Role: `finance_lead`
   - Query: `"What is the company's financial situation and gross margin?"`
   - *Result*: Authorized and decrypted with AES-256-GCM; Q3 gross margin (34.2%) and runway are reported.

## Evaluation & Benchmarks

Execute the automated evaluation suite to benchmark retrieval quality, security defenses, and latency telemetry:

```bash
python scripts/evaluate.py
```

The benchmark saves real measured results to [`reports/evaluation.json`](file:///d:/Selfprojects/securerag/reports/evaluation.json) and generates a detailed report at [`reports/evaluation.md`](file:///d:/Selfprojects/securerag/reports/evaluation.md).

### Measured Results Summary

#### 1. Retrieval Quality (Hybrid Dense + Sparse RRF)
| Metric | Measured Value | Target / Baseline |
| :--- | :--- | :--- |
| **Recall@1** | **100.00%** | ≥ 80.00% |
| **Recall@3** | **100.00%** | ≥ 90.00% |
| **Recall@5** | **100.00%** | ≥ 95.00% |
| **Precision@1** | **100.00%** | ≥ 80.00% |
| **Precision@3** | **33.33%** | Baseline |
| **Precision@5** | **20.00%** | Baseline |
| **Mean Reciprocal Rank (MRR)** | **1.0000** | ≥ 0.8500 |

#### 2. Security Hardening
| Security Dimension | Measured Value | Invariant / Target |
| :--- | :--- | :--- |
| **Unauthorized Retrieval Rate** | **0.00%** (0 / 46 leaks) | 0.00% (Strict Invariant) |
| **Prompt-Injection Detection Rate** | **100.00%** (20 / 20 TP) | ≥ 90.00% |
| **False-Positive Rate (FPR)** | **0.00%** (0 / 20 FP) | ≤ 10.00% |
| **Sanitizer F1 Score** | **1.0000** | ≥ 0.9000 |

#### 3. Latency Telemetry (Offline Deterministic Mode)
| Pipeline Stage | P50 (Median) | P95 | Mean |
| :--- | :--- | :--- | :--- |
| **Embedding Latency** | **6.72 ms** | 9.90 ms | 7.28 ms |
| **Retrieval Latency** | **11.54 ms** | 19.13 ms | 12.93 ms |
| **Generation Latency (Mock)** | **0.02 ms** | 0.02 ms | 0.01 ms |
| **End-to-End Latency** | **15.66 ms** | 19.79 ms | 16.23 ms |

> Note: With live LLM providers (Groq/OpenAI/Anthropic/Gemini), generation latency is governed by network roundtrip and remote model inference (typically 200ms–1500ms). Local embedding and retrieval remain consistent at ~10ms–30ms.

## Running Tests

Execute the automated test suite:

```bash
pytest -v
```

All 38 unit and integration tests verify API endpoints, AES-256-GCM encryption, dual-stage RBAC, prompt-injection sanitization, hybrid retrieval, and evaluation metrics.
