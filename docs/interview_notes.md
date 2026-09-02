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
