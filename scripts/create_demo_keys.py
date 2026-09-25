"""Generate the hosted demo's API keys (guest, employee, exec).

    python scripts/create_demo_keys.py --hashed-out data/demo_api_keys.hashed.json

* The plaintext keys are printed ONCE as JSON {"role": "key"}. Put that JSON in the GitHub secret
  DEMO_KEYS_JSON (used by the deploy smoke test) and, if you want, on the demo page.
* The hashed keys file (SHA-256 only) is what goes into SSM:
      aws ssm put-parameter --name /securerag/api_keys_json --type SecureString \\
          --value file://data/demo_api_keys.hashed.json --overwrite --region ap-south-1

The demo corpus is synthetic, so publishing these keys only lets visitors try each role;
reserved concurrency, per-key rate limits and the LLM's free tier bound the cost (SECURITY.md).
"""
import argparse
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.security.auth import hash_api_key  # noqa: E402
from securerag.security.rbac import ROLE_POLICY  # noqa: E402

DEMO_ROLES = ("guest", "employee", "exec")


def build(roles: tuple[str, ...] = DEMO_ROLES) -> tuple[dict[str, str], dict]:
    plain: dict[str, str] = {}
    entries = []
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    for role in roles:
        if role not in ROLE_POLICY:
            raise ValueError(f"unknown role {role}")
        key = f"srag_demo_{role}_" + secrets.token_urlsafe(24)
        plain[role] = key
        entries.append({"id": f"demo-{role}", "hash": hash_api_key(key), "principal": f"demo-{role}",
                        "role": role, "created_at": stamp})
    return plain, {"keys": entries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hashed-out", default="data/demo_api_keys.hashed.json",
                        help="where to write the hashed keys JSON (git-ignored under data/)")
    parser.add_argument("--roles", nargs="+", default=list(DEMO_ROLES), choices=sorted(ROLE_POLICY))
    args = parser.parse_args(argv)

    plain, hashed = build(tuple(args.roles))
    out = Path(args.hashed_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(hashed, indent=2), encoding="utf-8")
    print(f"Hashed keys written to {out} -> paste into SSM /securerag/api_keys_json", file=sys.stderr)
    print("Plaintext keys (shown once) -> GitHub secret DEMO_KEYS_JSON:", file=sys.stderr)
    print(json.dumps(plain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
