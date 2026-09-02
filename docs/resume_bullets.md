# Resume bullets

**SecureRAG — Security-Hardened Enterprise RAG System**  
*Python, LangGraph, FastAPI, ChromaDB, BM25, Sentence Transformers, AES-256-GCM, FastAPI*

- Built a security-aware enterprise RAG service with **LangGraph** orchestration, combining dense/sparse retrieval, RBAC-based authorization, encrypted document storage, prompt-injection sanitization, and grounded LLM generation.
- Implemented **role- and clearance-aware retrieval** across departmental document boundaries, with authorization enforced before document decryption and structured request-level audit logs for traceability.
- Added an interactive browser UI and automated evaluation suite covering retrieval quality, unauthorized retrieval, injection detection, and end-to-end latency.

## Conservative version

- Developed a LangGraph-based RAG pipeline with ChromaDB, Sentence Transformers, RBAC, AES-256-GCM encrypted document payloads, prompt-injection sanitization, and JSONL audit logging.
- Added hybrid retrieval using ChromaDB dense search and BM25 sparse search with rank fusion, plus a FastAPI API and browser UI for role-based querying.
