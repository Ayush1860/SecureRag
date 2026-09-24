"""Verify the audit log hash chain across all rotated files.

    python scripts/verify_audit.py                 # AUDIT_LOG_PATH
    python scripts/verify_audit.py path/to/audit.jsonl

Exit code 0 if the chain is intact, 1 if it is broken (the first bad entry is printed).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.config import get_settings  # noqa: E402
from securerag.security.audit import verify_chain  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", help="audit log path (default: AUDIT_LOG_PATH)")
    args = parser.parse_args(argv)
    path = args.path or get_settings().audit_log_path
    result = verify_chain(path)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
