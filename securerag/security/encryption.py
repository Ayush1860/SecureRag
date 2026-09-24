"""AES-256-GCM payload encryption with a keyring, key IDs and associated data.

Ciphertext formats
    v2 (current): ``v2:<key_id>:<b64(nonce(12) || tag(16) || ciphertext)>``, encrypted under the
                  keyring's *active* key, optionally with associated data (AAD).
    v1 (legacy):  ``<b64(nonce || tag || ciphertext)>`` with no key ID and no AAD. Still decrypted
                  (every key in the keyring is tried) so stores built before v2 keep working;
                  ``scripts/rotate_key.py --all`` upgrades them.

Chunk payloads are bound to their identity and RBAC labels with AAD =
``chunk_aad(chunk_id, department, clearance)``. A ciphertext copied onto another chunk, or whose
metadata was relabelled (e.g. confidential -> public), fails authentication instead of decrypting.

Keyring sources, first match wins:
    SECURERAG_KEYRING_FILE   path to JSON  {"active": "k2", "index": "k1", "keys": {"k1": "<b64>", ...}}
    SECURERAG_KEYRING        the same JSON inline
    SECURERAG_AES_KEY_B64    a single key (its ID is a fingerprint of the key)
    (none)                   an ephemeral random key, for tests and throwaway runs only

The *index* key (default: the active key of a single-key setup, or ``"index"`` in the keyring)
roots the HMAC keys for chunk IDs and sparse terms. It stays fixed across rotations so IDs and
indexes remain valid; rotating it requires ``ingest --full-rebuild``.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF
from Crypto.Random import get_random_bytes

logger = logging.getLogger(__name__)

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_V2_PREFIX = "v2:"


class DecryptionError(ValueError):
    """Raised when ciphertext authentication or decryption fails."""
    pass


def _decode_key(encoded: str, source: str) -> bytes:
    try:
        key = base64.b64decode(encoded.strip(), validate=True)
    except Exception as e:
        raise ValueError(f"{source} is not valid base64: {e}") from e
    if len(key) != 32:
        raise ValueError(f"{source} must decode to exactly 32 bytes (got {len(key)})")
    return key


def key_fingerprint(key: bytes) -> str:
    """Short non-secret identifier derived from a key (safe to store in metadata)."""
    sub = HKDF(key, 32, salt=b"securerag", hashmod=SHA256, context=b"securerag/key-id/v1")
    return hmac.new(sub, b"key-id", hashlib.sha256).hexdigest()[:12]


def chunk_aad(chunk_id: str, department: str, clearance: str) -> bytes:
    """Associated data binding a payload to its chunk ID and RBAC labels."""
    return f"securerag/chunk/v2|{chunk_id}|{department}|{clearance}".encode("utf-8")


def aad_for(chunk_id: str, metadata: dict) -> bytes:
    return chunk_aad(chunk_id, str(metadata.get("department", "")), str(metadata.get("clearance", "")))


@dataclass
class Keyring:
    keys: dict[str, bytes]
    active_id: str
    index_id: str
    ephemeral: bool = False

    def __post_init__(self) -> None:
        for kid, key in self.keys.items():
            if not _KEY_ID_RE.match(kid):
                raise ValueError(f"invalid key id {kid!r}: use 1-40 chars of [A-Za-z0-9_-]")
            if len(key) != 32:
                raise ValueError(f"key {kid!r} must be 32 bytes")
        for role, kid in (("active", self.active_id), ("index", self.index_id)):
            if kid not in self.keys:
                raise ValueError(f"{role} key id {kid!r} is not in the keyring")

    @classmethod
    def single(cls, key: bytes, ephemeral: bool = False) -> "Keyring":
        kid = key_fingerprint(key)
        return cls(keys={kid: key}, active_id=kid, index_id=kid, ephemeral=ephemeral)

    @classmethod
    def from_json(cls, data: dict, source: str) -> "Keyring":
        keys = {kid: _decode_key(v, f"{source} key {kid!r}") for kid, v in (data.get("keys") or {}).items()}
        if not keys:
            raise ValueError(f"{source} contains no keys")
        active = data.get("active")
        if not active:
            raise ValueError(f"{source} must name the active key")
        return cls(keys=keys, active_id=active, index_id=data.get("index") or active)

    @classmethod
    def from_env(cls) -> "Keyring":
        path = os.getenv("SECURERAG_KEYRING_FILE")
        if path:
            with open(path, encoding="utf-8") as fh:
                return cls.from_json(json.load(fh), f"SECURERAG_KEYRING_FILE ({path})")
        inline = os.getenv("SECURERAG_KEYRING")
        if inline:
            return cls.from_json(json.loads(inline), "SECURERAG_KEYRING")
        single = os.getenv("SECURERAG_AES_KEY_B64")
        if single:
            return cls.single(_decode_key(single, "SECURERAG_AES_KEY_B64"))
        logger.warning(
            "SECURERAG_AES_KEY_B64 not configured; generated ephemeral random AES key. "
            "Persisted data cannot be decrypted across restarts without saving this key."
        )
        return cls.single(get_random_bytes(32), ephemeral=True)


class VectorStoreEncryptor:
    """
    Application-level document encryptor utilizing AES-256-GCM (random 96-bit nonces).

    Provides confidentiality and cryptographic integrity for chunk payloads at rest, with key
    IDs for rotation and optional associated data binding payloads to chunk identity/labels.
    """

    def __init__(self, key_b64: str | None = None, keyring: Keyring | None = None,
                 allow_legacy_v1: bool | None = None):
        # Legacy v1 blobs carry no AAD, so a v1 blob copied over another chunk would still decrypt.
        # Once a store is fully migrated (rotate_key.py --all), set SECURERAG_ALLOW_V1=false.
        if allow_legacy_v1 is None:
            allow_legacy_v1 = os.getenv("SECURERAG_ALLOW_V1", "true").lower() not in ("0", "false", "no")
        self.allow_legacy_v1 = allow_legacy_v1
        if keyring is not None:
            self.keyring = keyring
        elif key_b64:
            self.keyring = Keyring.single(_decode_key(key_b64, "SECURERAG_AES_KEY_B64"))
        else:
            self.keyring = Keyring.from_env()
        self._index_fingerprint = key_fingerprint(self.keyring.keys[self.keyring.index_id])

    # ------------------------------------------------------------------ key info
    @property
    def is_ephemeral(self) -> bool:
        return self.keyring.ephemeral

    @property
    def key(self) -> bytes:
        """The active key."""
        return self.keyring.keys[self.keyring.active_id]

    @property
    def key_id(self) -> str:
        """ID of the active (encrypting) key."""
        return self.keyring.active_id

    @property
    def index_key_id(self) -> str:
        """Fingerprint of the index root key; identifies which HMAC key space a store uses."""
        return self._index_fingerprint

    def export_key_b64(self) -> str:
        return base64.b64encode(self.key).decode("ascii")

    def derive_subkey(self, label: str) -> bytes:
        """32-byte subkey for ``label`` via HKDF-SHA256 over the index root key.

        Rooted in the index key (not the active key) so HMAC-derived chunk IDs and sparse terms
        survive rotation of the encryption key.
        """
        root = self.keyring.keys[self.keyring.index_id]
        return HKDF(root, 32, salt=b"securerag", hashmod=SHA256, context=label.encode("utf-8"))

    # ------------------------------------------------------------------ crypto
    def encrypt(self, plaintext: str, aad: bytes | None = None) -> str:
        nonce = get_random_bytes(12)
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        if aad:
            cipher.update(aad)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
        body = base64.b64encode(nonce + tag + ciphertext).decode("ascii")
        return f"{_V2_PREFIX}{self.key_id}:{body}"

    @staticmethod
    def blob_key_id(blob: str) -> str | None:
        """Key ID of a v2 blob, or None for legacy v1 blobs."""
        if blob.startswith(_V2_PREFIX):
            parts = blob.split(":", 2)
            return parts[1] if len(parts) == 3 else None
        return None

    def decrypt(self, blob: str, aad: bytes | None = None) -> str:
        try:
            if blob.startswith(_V2_PREFIX):
                parts = blob.split(":", 2)
                if len(parts) != 3:
                    raise DecryptionError("malformed v2 ciphertext")
                key = self.keyring.keys.get(parts[1])
                if key is None:
                    raise DecryptionError(f"ciphertext was encrypted with unknown key id {parts[1]!r}")
                return self._open(key, parts[2], aad)
            # Legacy v1: no key id, no AAD. Try every key in the ring.
            if not self.allow_legacy_v1:
                raise DecryptionError("legacy v1 ciphertext rejected (SECURERAG_ALLOW_V1=false)")
            last: Exception | None = None
            for key in self.keyring.keys.values():
                try:
                    return self._open(key, blob, None)
                except Exception as exc:  # noqa: BLE001
                    last = exc
            raise DecryptionError("no key in the keyring authenticates this v1 ciphertext") from last
        except DecryptionError:
            raise
        except Exception as exc:
            raise DecryptionError(
                "Payload authentication/MAC verification failed. This indicates data tampering "
                "or a mismatch between the current AES key and the key used during ingestion."
            ) from exc

    @staticmethod
    def _open(key: bytes, body_b64: str, aad: bytes | None) -> str:
        raw = base64.b64decode(body_b64)
        if len(raw) < 28:
            raise DecryptionError(f"Ciphertext blob is too short ({len(raw)} bytes < 28 min header)")
        nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        if aad:
            cipher.update(aad)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")


INDEX_KEY_LABEL = "securerag/index/v1"


def keyed_hash(key: bytes, *parts: str, length: int = 32) -> str:
    """HMAC-SHA256 over ``parts`` joined by NUL, hex-truncated to ``length``.

    Used for chunk IDs, content hashes and file fingerprints so values derived from plaintext
    cannot be confirmed by someone who can read the store but does not hold the key.
    """
    mac = hmac.new(key, "\x00".join(parts).encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:length]
