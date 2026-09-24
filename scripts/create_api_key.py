"""Create an API key for a principal and add its SHA-256 hash to the keys file.

    python scripts/create_api_key.py --principal alice --role employee
    python scripts/create_api_key.py --principal ci-bot --role guest --file data/api_keys.json

The plaintext key is printed once and never stored.
"""
import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.config import get_settings  # noqa: E402
from securerag.security.auth import hash_api_key  # noqa: E402
from securerag.security.rbac import ROLE_POLICY  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--role", required=True, choices=sorted(ROLE_POLICY))
    parser.add_argument("--file", help="keys file (default: API_KEYS_FILE)")
    args = parser.parse_args(argv)

    path = Path(args.file or get_settings().api_keys_file)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"keys": []}
    key = "srag_" + secrets.token_urlsafe(32)
    key_id = f"{args.principal}-{secrets.token_hex(3)}"
    data["keys"].append({
        "id": key_id,
        "hash": hash_api_key(key),
        "principal": args.principal,
        "role": args.role,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Created key {key_id} for {args.principal} ({args.role}) in {path}")
    print(f"API key (shown once): {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
