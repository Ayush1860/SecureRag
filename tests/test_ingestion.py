import hashlib

import numpy as np
import pytest
from conftest import make_store

from securerag.config import Settings
from securerag.ingestion.chunker import chunk_text, whitespace_length
from securerag.ingestion.loaders import LoaderError, load_file
from securerag.ingestion.metadata import MetadataError, MetadataResolver
from securerag.ingestion.pipeline import IngestError, IngestPipeline
from securerag.ingestion.state import IngestState
from securerag.security.encryption import DecryptionError, VectorStoreEncryptor, aad_for

# --------------------------------------------------------------------------- helpers

class FakeEncoder:
    """Deterministic, model-free encoder so ingestion tests run offline and fast."""

    max_seq_length = None

    def __init__(self):
        self.calls = 0

    def encode(self, texts, **_kw):
        self.calls += 1
        out = []
        for t in texts:
            digest = hashlib.sha256(t.encode()).digest()
            v = np.frombuffer(digest[:32], dtype=np.uint8).astype(np.float32)[:16] + 1.0
            out.append(v / np.linalg.norm(v))
        return np.array(out)


def _sentences(n: int, tag: str) -> str:
    return " ".join(f"{tag} sentence number {i} talks about warehouse robots." for i in range(n))


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    _write(data, "general/public/a.txt", _sentences(12, "alpha"))
    _write(data, "finance/confidential/b.txt", _sentences(20, "bravo") + "\n\n" + _sentences(5, "bravo2"))
    _write(data, "hr/internal/c.md", "# Leave\n\n" + _sentences(8, "charlie"))
    settings = Settings(chunk_size_tokens=40, chunk_overlap_tokens=8, ingest_batch_size=16, embed_model="fake-model")
    encryptor = VectorStoreEncryptor()

    def make_pipeline(enc=encryptor, s=settings):
        store = make_store(tmp_path.name)
        state = IngestState(tmp_path / "state.sqlite")
        return IngestPipeline(s, enc, store, state, encoder=FakeEncoder(), length_fn=whitespace_length)

    return {"data": data, "settings": settings, "encryptor": encryptor, "make": make_pipeline}


def _all_ids(pipeline):
    return sorted(cid for batch in pipeline.store.iter_all() for cid, _, _ in batch)


def _ids_by_path(pipeline):
    out: dict[str, set[str]] = {}
    for batch in pipeline.store.iter_all():
        for cid, _, meta in batch:
            out.setdefault(meta["path"], set()).add(cid)
    return out


# --------------------------------------------------------------------------- chunker

def test_chunker_respects_max_tokens_and_offsets():
    text = "\n\n".join(_sentences(6, f"para{p}") for p in range(5))
    chunks = chunk_text(text, max_tokens=30, overlap_tokens=6, length_fn=whitespace_length)
    assert len(chunks) > 3
    for i, c in enumerate(chunks):
        assert c.index == i
        assert c.text == text[c.start:c.end]
        assert whitespace_length(c.text) <= 30
        # Boundaries fall on whole words.
        assert c.start == 0 or text[c.start - 1].isspace()
        assert c.end == len(text) or text[c.end].isspace()
    # Every word of the source is covered.
    covered = set()
    for c in chunks:
        covered.update(range(c.start, c.end))
    assert all(i in covered for i, ch in enumerate(text) if not ch.isspace())


def test_chunker_overlap_between_consecutive_chunks():
    text = _sentences(20, "ov")
    chunks = chunk_text(text, max_tokens=40, overlap_tokens=10, length_fn=whitespace_length)
    assert len(chunks) > 2
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start < prev.end, "consecutive chunks should overlap"
        assert whitespace_length(text[nxt.start:prev.end]) <= 10


def test_chunker_prefers_sentence_boundaries():
    text = _sentences(10, "s")
    chunks = chunk_text(text, max_tokens=25, overlap_tokens=0, length_fn=whitespace_length)
    for c in chunks[:-1]:
        assert c.text.endswith(".")


def test_chunker_splits_oversized_sentence_by_words_and_chars():
    long_sentence = " ".join(f"w{i}" for i in range(100))
    chunks = chunk_text(long_sentence, max_tokens=15, overlap_tokens=0, length_fn=whitespace_length)
    assert all(whitespace_length(c.text) <= 15 for c in chunks)
    blob = "x" * 500
    char_chunks = chunk_text(blob, max_tokens=10, overlap_tokens=0, length_fn=lambda t: (len(t) + 9) // 10)
    assert "".join(c.text for c in char_chunks) == blob


def test_chunker_rejects_bad_overlap():
    with pytest.raises(ValueError):
        chunk_text("abc", max_tokens=10, overlap_tokens=10)


# --------------------------------------------------------------------------- metadata

def test_metadata_folder_convention(tmp_path):
    p = _write(tmp_path, "engineering/internal/x.txt", "hi")
    labels = MetadataResolver(tmp_path).resolve(p)
    assert (labels.department, labels.clearance, labels.origin) == ("engineering", "internal", "folder")


def test_metadata_precedence_sidecar_over_manifest_over_folder(tmp_path):
    p = _write(tmp_path, "general/public/x.txt", "hi")
    (tmp_path / "manifest.csv").write_text("path,department,clearance\ngeneral/public/x.txt,hr,internal\n",
                                           encoding="utf-8")
    assert MetadataResolver(tmp_path).resolve(p).origin == "manifest"
    assert MetadataResolver(tmp_path).resolve(p).department == "hr"

    _write(tmp_path, "general/public/x.txt.meta.yaml", "department: finance\nclearance: confidential\n")
    labels = MetadataResolver(tmp_path).resolve(p)
    assert (labels.department, labels.clearance, labels.origin) == ("finance", "confidential", "sidecar")


@pytest.mark.parametrize("rel, sidecar", [
    ("loose.txt", None),                                   # no labels at all
    ("marketing/public/x.txt", None),                      # unknown department
    ("general/secret/x.txt", None),                        # unknown clearance
    ("general/public/y.txt", "department: general\n"),     # sidecar missing clearance
    ("general/public/z.txt", "- not\n- a mapping\n"),      # malformed sidecar
])
def test_metadata_fails_closed(tmp_path, rel, sidecar):
    p = _write(tmp_path, rel, "hi")
    if sidecar is not None:
        _write(tmp_path, rel + ".meta.yaml", sidecar)
    with pytest.raises(MetadataError):
        MetadataResolver(tmp_path).resolve(p)


# --------------------------------------------------------------------------- loaders

def test_loaders_text_markdown_html_docx(tmp_path):
    assert load_file(_write(tmp_path, "a.txt", "plain")) == "plain"
    assert "Title" in load_file(_write(tmp_path, "a.md", "# Title\n\nbody"))
    html = load_file(_write(tmp_path, "a.html", "<html><script>evil()</script><p>Hello</p><p>World</p></html>"))
    assert "Hello" in html and "World" in html and "evil" not in html

    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Docx paragraph one")
    d.add_paragraph("Docx paragraph two")
    d.save(tmp_path / "a.docx")
    text = load_file(tmp_path / "a.docx")
    assert "paragraph one" in text and "paragraph two" in text


def test_loader_unknown_and_corrupt(tmp_path):
    with pytest.raises(LoaderError):
        load_file(_write(tmp_path, "a.xyz", "?"))
    pytest.importorskip("pypdf")
    with pytest.raises(LoaderError):
        load_file(_write(tmp_path, "broken.pdf", "not a pdf"))


# --------------------------------------------------------------------------- pipeline

def test_ingest_writes_encrypted_chunks_with_metadata(env):
    pipeline = env["make"]()
    report = pipeline.run(env["data"])
    assert report.indexed_new == 3 and report.rejected == 0
    assert pipeline.store.count() == report.chunks_written > 3

    for batch in pipeline.store.iter_all():
        for cid, payload, meta in batch:
            assert "sentence" not in payload  # ciphertext only
            assert payload.startswith(f"v2:{env['encryptor'].key_id}:")
            assert "sentence" in env["encryptor"].decrypt(payload, aad=aad_for(cid, meta))
            with pytest.raises(DecryptionError):  # payload is bound to its chunk id + labels
                env["encryptor"].decrypt(payload)
            for key in ("doc_id", "chunk_index", "content_hash", "source", "department", "clearance",
                        "key_id", "embed_model", "ingested_at", "injection_flagged"):
                assert key in meta
            assert meta["key_id"] == env["encryptor"].key_id
            assert meta["embed_model"] == "fake-model"


def test_ingest_is_idempotent(env):
    first = env["make"]()
    first.run(env["data"])
    ids = _all_ids(first)

    second = env["make"]()
    report = second.run(env["data"])
    assert report.unchanged == 3 and report.indexed_new == 0 and report.chunks_written == 0
    assert _all_ids(second) == ids
    assert second.encoder.calls == 0


def test_ingest_incremental_update_only_touches_changed_file(env):
    pipeline = env["make"]()
    pipeline.run(env["data"])
    before = _ids_by_path(pipeline)

    _write(env["data"], "general/public/a.txt", _sentences(12, "alpha-edited"))
    report = env["make"]().run(env["data"])
    assert report.indexed_changed == 1 and report.unchanged == 2
    after = _ids_by_path(pipeline)
    assert after["finance/confidential/b.txt"] == before["finance/confidential/b.txt"]
    assert after["hr/internal/c.md"] == before["hr/internal/c.md"]
    assert after["general/public/a.txt"].isdisjoint(before["general/public/a.txt"])
    # No orphans: store holds exactly the current chunks.
    assert pipeline.store.count() == sum(len(v) for v in after.values())


def test_ingest_handles_deleted_files(env):
    pipeline = env["make"]()
    pipeline.run(env["data"])
    (env["data"] / "hr/internal/c.md").unlink()
    report = env["make"]().run(env["data"])
    assert report.deleted_docs == 1 and report.chunks_deleted > 0
    assert "hr/internal/c.md" not in _ids_by_path(pipeline)


def test_ingest_rejects_unlabelled_and_removes_previously_indexed(env):
    _write(env["data"], "orphan.txt", "no labels here")
    pipeline = env["make"]()
    report = pipeline.run(env["data"])
    assert report.rejected == 1
    assert all(p != "orphan.txt" for p in _ids_by_path(pipeline))

    # Relabel an indexed file with an invalid sidecar: its chunks must disappear (fail closed).
    _write(env["data"], "general/public/a.txt.meta.yaml", "department: nowhere\nclearance: public\n")
    report = env["make"]().run(env["data"])
    assert report.rejected == 2
    assert "general/public/a.txt" not in _ids_by_path(pipeline)


def test_relabel_via_sidecar_reindexes_with_new_labels(env):
    pipeline = env["make"]()
    pipeline.run(env["data"])
    _write(env["data"], "general/public/a.txt.meta.yaml", "department: finance\nclearance: confidential\n")
    report = env["make"]().run(env["data"])
    assert report.indexed_changed == 1
    metas = [m for batch in pipeline.store.iter_all() for _, _, m in batch if m["path"] == "general/public/a.txt"]
    assert metas and all((m["department"], m["clearance"]) == ("finance", "confidential") for m in metas)


def test_unsupported_files_are_skipped(env):
    _write(env["data"], "general/public/image.png", "binary")
    report = env["make"]().run(env["data"])
    assert report.skipped_unsupported == 1 and report.failed == 0


def test_upserts_respect_store_batch_limit(env):
    pipeline = env["make"]()
    pipeline.store.max_batch = 3
    seen_sizes = []
    real_write = pipeline.store._write

    def spy(ids, *rest):
        seen_sizes.append(len(ids))
        return real_write(ids, *rest)

    pipeline.store._write = spy
    report = pipeline.run(env["data"])
    assert max(seen_sizes) <= 3
    assert sum(seen_sizes) == report.chunks_written


def test_full_rebuild_and_dry_run(env):
    pipeline = env["make"]()
    pipeline.run(env["data"])
    count = pipeline.store.count()

    _write(env["data"], "exec/confidential/d.txt", _sentences(4, "delta"))
    dry = env["make"]().run(env["data"], dry_run=True)
    assert dry.indexed_new == 1 and dry.unchanged == 3
    assert pipeline.store.count() == count  # dry run wrote nothing

    rebuilt = env["make"]().run(env["data"], full_rebuild=True)
    assert rebuilt.indexed_new == 4 and rebuilt.unchanged == 0


def test_ingest_refuses_store_built_with_another_key(env):
    env["make"]().run(env["data"])
    other = VectorStoreEncryptor()
    with pytest.raises(IngestError, match="full-rebuild"):
        env["make"](enc=other).run(env["data"])
    # A full rebuild with the new key is allowed.
    report = env["make"](enc=other).run(env["data"], full_rebuild=True)
    assert report.indexed_new == 3


def test_parallel_workers_produce_same_ids(env):
    pipeline = env["make"]()
    pipeline.run(env["data"], workers=4)
    parallel_ids = _all_ids(pipeline)
    serial = env["make"]()
    report = serial.run(env["data"], full_rebuild=True, workers=1)
    assert report.indexed_new == 3
    assert _all_ids(serial) == parallel_ids
