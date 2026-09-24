# Security model

SecureRAG is a reference implementation, not a certified product. This document states what it
protects, against whom, how, and what it deliberately does **not** protect against.

## Invariants

1. **A caller never retrieves, decrypts or sees a chunk outside its role's policy.** The role
   comes only from the authenticated principal, and RBAC is enforced at four points: the vector
   store pre-filter, the sparse index (only the permitted partitions are ever opened), the
   post-fusion metadata check, and the `authorize` graph node before any decryption.
2. **Payloads at rest are AES-256-GCM** with random 96-bit nonces, key-ID-tagged (`v2:<kid>:...`)
   and bound by associated data to `(chunk_id, department, clearance)`.
3. **Retrieved text is data, never instructions.** It sits inside a data-only preamble; chunks
   flagged at ingest are wrapped in `[UNTRUSTED_DOCUMENT_CONTENT]` delimiters.
4. **No corpus plaintext is held in memory.** Only the authorized top-k of the current request is
   decrypted, and only for the duration of that request.

## Assets

| Asset | Where it lives |
|---|---|
| Document text | Vector store payloads (ciphertext), process memory during one request |
| Embeddings | Vector store (plaintext vectors) |
| Sparse index | `SPARSE_DIR`, per-partition bm25s files with HMAC-hashed terms |
| Ingestion state | `*_state.sqlite`: doc paths, keyed fingerprints, chunk IDs, hashed terms |
| Encryption keys | `SECURERAG_AES_KEY_B64` / keyring file (from your secret manager) |
| API keys / JWT secret | `API_KEYS_FILE` (SHA-256 hashes only), `JWT_SECRET` / public key |
| Audit log | `AUDIT_LOG_PATH` (+ rotated files) |

## Threat model

| # | Threat / attacker | Control | Residual risk |
|---|---|---|---|
| T1 | Any API client claims a higher role (`"role": "exec"` in the body) | Role comes only from the principal (API key / JWT). `QueryRequest` forbids extra fields, so a body `role` is a 422. The dev header is refused unless `ENV=dev`. | Misconfiguring a production deployment with `ENV=dev AUTH_MODE=dev`. The service logs a loud warning at startup. |
| T2 | Stolen or forged credentials | API keys stored as SHA-256 hashes. JWT algorithm pinned (no `none`, no HS/RS confusion). `exp`/`sub` required, `aud`/`iss` checked when set, HS256 secret ≥ 32 bytes. Per-principal rate limiting. | A stolen valid key or token works until it is revoked or expires. There's no key expiry and no revocation list beyond removing a key from the file. |
| T3 | Low-clearance user tries to pull confidential text through retrieval (lexical or semantic probing) | Dense search is pre-filtered per partition, and the sparse index is physically partitioned (other partitions are never loaded). Post-fusion metadata check, then `authorize` before decryption. The rerank node only decrypts authorized candidates. Benchmarks track canary leaks per role (always 0 so far). | Bugs in the filter code. Four independent layers must all fail for a leak. |
| T4 | Side channel via sparse scores: global IDF would reveal term statistics of confidential partitions | IDF is computed per partition; a query only ever scores partitions its role can read. | None known for scores. Timing differences between roles reveal how many partitions a role can read, which is not sensitive. |
| T5 | Attacker with read access to the vector DB / disk | Payloads are AES-GCM ciphertext. Chunk IDs, content hashes and file fingerprints are HMAC-keyed, so a guessed plaintext can't be confirmed offline. Sparse terms are HMAC-hashed. | **Embedding inversion:** vectors are stored in plaintext, and inversion attacks can partially reconstruct text from embeddings. **Term-frequency analysis:** hashed terms keep their frequencies, so an attacker with a reference corpus can guess common words. The metadata (department, clearance, source file name, chunk index, token count) is plaintext. |
| T6 | Attacker with write access to the vector DB relabels a confidential chunk as public, or swaps ciphertexts between chunks | AAD binds each payload to `(chunk_id, department, clearance)`, so relabelled or swapped payloads fail GCM authentication. Startup decrypts a random sample and refuses to run on failure. | Deleting chunks (availability). Legacy v1 blobs (no AAD) accept swaps until `rotate_key.py --all` has run and `SECURERAG_ALLOW_V1=false` is set. |
| T7 | Wrong or rotated key at startup | `verify_store` checks the index key ID and decrypts a sample, then **fails fast**. There is no silent rebuild (the old code wiped and re-indexed on a key mismatch). | None: an operator has to act. |
| T8 | Key compromise / scheduled rotation | Keyring with key IDs. `scripts/rotate_key.py` re-encrypts in resumable batches; old blobs decrypt with their own key ID in the meantime. | The **index key** (root of the HMAC IDs and hashed terms) isn't rotated by `rotate_key.py`, because rotating it means re-indexing with `ingest --full-rebuild`. |
| T9 | Indirect prompt injection planted in documents | Regex heuristics run at ingest, plus an optional ML classifier (`INJECTION_CLASSIFIER`). The flag is stored per chunk, flagged chunks are wrapped as untrusted, and the system prompt forbids following instructions found in context. | Heuristics and classifiers can be evaded (paraphrase, encoding, other languages). A capable model can still be swayed. Treat LLM output as untrusted. |
| T10 | Tampering with the audit log to hide access | Hash chain (`prev_hash` → `entry_hash`) across rotated files; `scripts/verify_audit.py` reports the first broken entry. Writes are serialised with a file lock. | Tamper-*evident*, not tamper-proof: someone who can rewrite the whole log can recompute the chain. Ship the log or periodic head hashes to write-once storage. |
| T11 | Information leaks through errors | 500s return a generic message plus a `request_id`; details go to server logs only. | Server logs are sensitive and need access control. |
| T12 | Resource exhaustion | Request body limit (`MAX_REQUEST_BYTES`, requires `Content-Length`), query length ≤ 4000 characters, `top_k` ≤ 10, per-principal rate limit, bounded context token budget. | The in-memory rate limiter is per worker; for multi-worker deployments use `RATE_LIMIT_STORAGE=redis://...`. |
| T13 | Cross-site requests from other origins | CORS allows only `CORS_ORIGINS` (empty = same origin), with no credentials and no wildcard. Auth travels in headers, not cookies. | — |
| T14 | Poisoned or mislabelled source documents | Labels come from a sidecar, then `manifest.csv`, then the folder convention, and are validated. Missing or invalid labels mean the file is rejected (fail closed). Relabelling or rejecting a document removes its old chunks. | Whoever controls the source folder controls the labels. Protect the ingestion input like the data itself. |

## Known limitations (summary)

- Embedding inversion (T5): the vectors aren't encrypted, so similarity search keeps working.
- Term-frequency analysis on the hashed sparse index (T5).
- The Streamlit demo has no authentication and refuses to run unless `ENV=dev`.
- The mock LLM is deterministic and meant for offline tests. A real provider sees the authorized
  context of each request, so choose it accordingly.

## Reporting

Please open a private security advisory on the repository rather than a public issue.
