# Security notes

This project is a portfolio/reference implementation, not a certified enterprise security product.

The prompt-injection defense is intentionally simple and pattern-based. Treat it as defense in depth; it does not guarantee detection of adversarial or obfuscated instructions.

AES-256-GCM protects stored document payloads at the application layer. The encryption key must be supplied from a secret-management system in a real deployment. Do not commit `.env` files or generated keys.

RBAC is metadata-driven and should be integrated with a trusted identity provider before production use.
