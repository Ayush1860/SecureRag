# Interview notes

## 1. Why secure RAG?
Enterprise documents can contain confidential data and untrusted text. A plain RAG pipeline can retrieve information a user should not see and can expose the model to instructions embedded inside documents.

## 2. Why RBAC before generation?
Authorization is applied to metadata filters during retrieval and repeated after retrieval. The goal is to keep unauthorized documents out of the generation context rather than relying on the model to hide them.

## 3. Why AES-GCM?
The project uses authenticated encryption for document payloads at rest. GCM provides confidentiality plus integrity/authentication for ciphertext.

## 4. Why hybrid retrieval?
Dense retrieval helps semantic matching while BM25 helps lexical/exact-term matching. Reciprocal Rank Fusion combines the ranked results without requiring score calibration across retrieval systems.

## 5. Why LangGraph?
The security boundaries are explicit, inspectable workflow nodes: retrieve, authorize, decrypt/sanitize, generate, and audit.

## 6. What is a limitation?
The prompt-injection detector is pattern-based, so it can miss obfuscated attacks and can produce false positives. It is a defense-in-depth component, not a proof of safety.

## 7. What does encryption cover?
The implementation encrypts stored document text. It does not claim to encrypt vector embeddings or metadata.

## 8. How does it scale without breaking the security model?
Ingestion streams files through bounded batches (embed, encrypt, upsert within the store's batch limit),
so memory stays flat as the corpus grows. It is incremental: keyed file fingerprints skip unchanged
files, changed files replace only their chunks, and deleted or relabelled files are removed. Startup
never decrypts the corpus. It proves the key on a random sample of 5 chunks, and only the authorized
top-k of each request is ever decrypted.

## 9. Why is the BM25 index partitioned by (department, clearance)?
Two reasons. RBAC becomes structural: a guest query never even opens a confidential partition's
index. And IDF stays per partition, because a global IDF would let a low-clearance user's scores depend
on term statistics of confidential documents, a small but real side channel.

## 10. Why HMAC the BM25 terms and chunk IDs?
With plain hashes, anyone who can read the index or the vector DB could confirm a guessed sentence
("salary of X is N") offline. A keyed hash stops that and scores stay identical. The residual risk is
term-frequency analysis, which SECURITY.md documents.

## 11. What stops someone from relabelling a confidential chunk as public in the vector DB?
AES-GCM associated data. Each payload is bound to `(chunk_id, department, clearance)`, so a relabelled
or swapped ciphertext fails authentication instead of decrypting for a guest. There is a test for it.

## 12. How is the role determined?
Only from the authenticated principal: an API key (stored as a SHA-256 hash) or a JWT (pinned algorithm,
`exp` required). A `role` in the request body is rejected with 422. The dev role switcher exists only
when `ENV=dev`.

## 13. How do you rotate keys?
Keyring with key IDs, and ciphertext in the form `v2:<kid>:...`. Old blobs decrypt with their own key
while `scripts/rotate_key.py` re-encrypts in resumable batches. The HMAC "index" key is separate, so
rotation doesn't change chunk IDs or indexes.

## 14. Is the audit log tamper-proof?
It is tamper-*evident*. Every entry carries the previous entry's hash, so editing or deleting a line
breaks the chain and `scripts/verify_audit.py` finds the first bad entry. Someone who can rewrite the
entire log could recompute the chain, so you ship head hashes to write-once storage.

## 15. What did the benchmarks teach you?
- The original ingest crashed at ~1,150 docs on Chroma's max batch size.
- Chroma's metadata filters scan every matching row, so filtered search grew linearly (100 ms p95 at
  31k chunks). That led to a bounded over-fetch that filters inside the store method, while Qdrant with
  payload indexes is the scale backend.
- Measured honestly, regex injection detection catches only the phrasings it was written for (~56%
  on paraphrased payloads), which is why an ML classifier hook exists.
