"""SecureRAG ingestion CLI.

    python scripts/ingest.py                      # incremental ingest of DATA_DIR
    python scripts/ingest.py --data-dir X          # ingest another folder into the same store
    python scripts/ingest.py --dry-run             # report what would change, write nothing
    python scripts/ingest.py --full-rebuild        # wipe the store and state, re-index everything
    python scripts/ingest.py --workers 4           # parallel file parsing / chunking
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.config import get_settings  # noqa: E402
from securerag.ingestion.pipeline import IngestError  # noqa: E402
from securerag.retrieval.store import run_ingestion  # noqa: E402
from securerag.security.encryption import VectorStoreEncryptor  # noqa: E402

logger = logging.getLogger("securerag.ingest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest documents into the SecureRAG encrypted store.")
    parser.add_argument("--data-dir", help="folder to ingest (default: DATA_DIR)")
    parser.add_argument("--chroma-dir", help="vector store folder (default: CHROMA_DIR)")
    parser.add_argument("--full-rebuild", action="store_true", help="wipe the store and state, then re-index")
    parser.add_argument("--dry-run", action="store_true", help="resolve, chunk and diff only; write nothing")
    parser.add_argument("--workers", type=int, default=1, help="threads for file parsing and chunking")
    parser.add_argument("--json", action="store_true", help="print the run report as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if args.chroma_dir:
        settings = settings.model_copy(update={"chroma_dir": args.chroma_dir})
    data_dir = args.data_dir or settings.data_dir

    encryptor = VectorStoreEncryptor()
    if encryptor.is_ephemeral and not args.dry_run:
        logger.error("SECURERAG_AES_KEY_B64 is not set. Ingesting with a throwaway key would make the store "
                     "unreadable after this process exits. Run scripts/generate_key.py and add the key to .env.")
        return 2

    progress = None
    bar = None
    try:
        from tqdm import tqdm

        bar = tqdm(unit="file", desc="Ingest", dynamic_ncols=True)
        progress = lambda _event, _info: bar.update(1)  # noqa: E731
    except ImportError:
        pass

    try:
        report = run_ingestion(settings, encryptor, data_dir=data_dir, full_rebuild=args.full_rebuild,
                               dry_run=args.dry_run, workers=args.workers, progress=progress)
    except IngestError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        if bar is not None:
            bar.close()

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        verb = "Would write" if args.dry_run else "Wrote"
        print(f"\nRun {report.run_id} over {report.data_dir} ({report.elapsed_s}s)")
        print(f"  files seen          : {report.files_seen}")
        print(f"  new / changed       : {report.indexed_new} / {report.indexed_changed}")
        print(f"  unchanged (skipped) : {report.unchanged}")
        print(f"  rejected (no labels): {report.rejected}")
        print(f"  failed to parse     : {report.failed}")
        print(f"  unsupported type    : {report.skipped_unsupported}")
        print(f"  deleted sources     : {report.deleted_docs}")
        print(f"  {verb + ' chunks':<20}: {report.chunks_written} (max {report.max_chunk_tokens} tokens each)")
        print(f"  chunks deleted      : {report.chunks_deleted}")
        print(f"  injection-flagged   : {report.injection_flagged_chunks}")
        for example in report.rejected_examples:
            print(f"  rejected: {example}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
