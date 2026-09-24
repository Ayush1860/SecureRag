"""Re-encrypt the store under the keyring's active key (resumable).

Rotation workflow:
  1. Add a new key to the keyring JSON and make it "active"; keep the old key(s) and the
     "index" key in the ring:  {"active": "k2", "index": "k1", "keys": {"k1": "...", "k2": "..."}}
  2. Restart the API (new chunks are now written under k2; old ones still decrypt under k1).
  3. python scripts/rotate_key.py            # re-encrypts every chunk not yet under k2
  4. Once it reports 0 remaining, the old key can be dropped from the ring (keep the index key).

  python scripts/rotate_key.py --all        # also re-encrypt current-key chunks and upgrade
                                            # legacy v1 blobs to v2 + AAD
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.config import get_settings  # noqa: E402
from securerag.ingestion.state import IngestState  # noqa: E402
from securerag.retrieval.store import open_vector_store  # noqa: E402
from securerag.security.encryption import VectorStoreEncryptor  # noqa: E402
from securerag.security.rotation import rotate_store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-encrypt stored chunks under the active key.")
    parser.add_argument("--all", action="store_true", help="include chunks already under the active key")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    encryptor = VectorStoreEncryptor()
    if encryptor.is_ephemeral:
        logging.error("no key configured (SECURERAG_KEYRING_FILE / SECURERAG_KEYRING / SECURERAG_AES_KEY_B64)")
        return 2
    store = open_vector_store(settings)
    state = IngestState(settings.resolved_state_db_path)
    try:
        report = rotate_store(store, state, encryptor, batch_size=args.batch_size, include_current=args.all)
    finally:
        state.close()
    print(json.dumps(report.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
