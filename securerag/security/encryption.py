import base64
import hashlib
import hmac
import os
import logging
from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF
from Crypto.Random import get_random_bytes

logger = logging.getLogger(__name__)


class DecryptionError(ValueError):
    """Raised when ciphertext authentication or decryption fails."""
    pass


class VectorStoreEncryptor:
    """
    Application-level document encryptor utilizing AES-256-GCM.
    
    Provides confidentiality and cryptographic integrity for chunk payloads at rest.
    Payload structure: nonce (12 bytes) || tag (16 bytes) || ciphertext.
    """

    def __init__(self, key_b64: str | None = None):
        encoded = key_b64 or os.getenv("SECURERAG_AES_KEY_B64")
        if encoded:
            try:
                key = base64.b64decode(encoded.strip())
            except Exception as e:
                raise ValueError(f"SECURERAG_AES_KEY_B64 is not valid base64: {e}") from e
            if len(key) != 32:
                raise ValueError(
                    f"SECURERAG_AES_KEY_B64 must decode to exactly 32 bytes (got {len(key)})"
                )
            self.key = key
            self.is_ephemeral = False
        else:
            self.key = get_random_bytes(32)
            self.is_ephemeral = True
            logger.warning(
                "SECURERAG_AES_KEY_B64 not configured; generated ephemeral random AES key. "
                "Persisted data cannot be decrypted across restarts without saving this key."
            )

    def encrypt(self, plaintext: str) -> str:
        nonce = get_random_bytes(12)
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
        return base64.b64encode(nonce + tag + ciphertext).decode("ascii")

    def decrypt(self, blob_b64: str) -> str:
        try:
            blob = base64.b64decode(blob_b64)
            if len(blob) < 28:
                raise DecryptionError(f"Ciphertext blob is too short ({len(blob)} bytes < 28 min header)")
            nonce, tag, ciphertext = blob[:12], blob[12:28], blob[28:]
            cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
        except DecryptionError:
            raise
        except Exception as exc:
            raise DecryptionError(
                "Payload authentication/MAC verification failed. This indicates data tampering "
                "or a mismatch between the current AES key and the key used during ingestion."
            ) from exc

    def export_key_b64(self) -> str:
        return base64.b64encode(self.key).decode("ascii")

    def derive_subkey(self, label: str) -> bytes:
        """32-byte subkey for ``label`` via HKDF-SHA256, independent of the AES key itself."""
        return HKDF(self.key, 32, salt=b"securerag", hashmod=SHA256, context=label.encode("utf-8"))

    @property
    def key_id(self) -> str:
        """Short non-secret identifier of the active key (safe to store in metadata)."""
        return hmac.new(self.derive_subkey("securerag/key-id/v1"), b"key-id", hashlib.sha256).hexdigest()[:12]


INDEX_KEY_LABEL = "securerag/index/v1"


def keyed_hash(key: bytes, *parts: str, length: int = 32) -> str:
    """HMAC-SHA256 over ``parts`` joined by NUL, hex-truncated to ``length``.

    Used for chunk IDs, content hashes and file fingerprints so values derived from plaintext
    cannot be confirmed by someone who can read the store but does not hold the key.
    """
    mac = hmac.new(key, "\x00".join(parts).encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:length]
