import json
import re

from scripts.benchmark_scale import ROLES, build_queries, render_markdown
from scripts.generate_corpus import CLEARANCES, DEPARTMENTS, generate_corpus

CANARY_RE = re.compile(r"CANARY-[A-Z]{3}-\d{6}")


def _read_all(root):
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.txt"))}


def test_generate_corpus_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate_corpus(a, docs=40, avg_words=80, seed=7)
    generate_corpus(b, docs=40, avg_words=80, seed=7)
    assert _read_all(a) == _read_all(b)

    generate_corpus(b, docs=40, avg_words=80, seed=8)
    assert _read_all(a) != _read_all(b)


def test_generate_corpus_layout_and_canaries(tmp_path):
    records = generate_corpus(tmp_path, docs=120, avg_words=60, seed=1, injection_rate=0.2)
    assert len(records) == 120
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["docs"] == 120

    for rec in records:
        path = tmp_path / rec.path
        department, clearance, _ = rec.path.split("/")
        assert department in DEPARTMENTS and clearance in CLEARANCES
        assert (department, clearance) == (rec.department, rec.clearance)
        canaries = CANARY_RE.findall(path.read_text(encoding="utf-8"))
        if rec.clearance == "confidential":
            assert canaries == [rec.canary]
        else:
            assert canaries == [] and rec.canary is None

    assert any(r.injected for r in records)


def test_build_queries_covers_every_role(tmp_path):
    generate_corpus(tmp_path, docs=30, avg_words=40, seed=3)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    queries = build_queries(manifest, per_role=20, seed=3)
    assert len(queries) == 20 * len(ROLES)
    assert {q["role"] for q in queries} == set(ROLES)
    assert queries == build_queries(manifest, per_role=20, seed=3)


def test_render_markdown_handles_failed_ingest():
    report = {
        "label": "x", "generated_at": "now", "git_commit": "abc", "data_dir": "d",
        "corpus": {"docs": 1, "words": 10, "canaries": 0, "injected": 0},
        "store_size_mb": 0.0,
        "ingest": {"ok": False, "process_wall_s": 1.0, "peak_rss_mb": 10.0, "error": "boom"},
        "serve": None,
    }
    md = render_markdown(report)
    assert "FAILED" in md and "boom" in md and "Skipped" in md
