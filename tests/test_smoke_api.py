"""scripts/smoke_api.py must flag leaks and spoofing, not just run."""
import scripts.smoke_api as smoke


def _fake_call(excerpt_clearance="public", role_in_response=None, body_role_status=422):
    def call(method, url, headers=None, body=None, timeout=90):
        if url.endswith("/api/auth/me"):
            return 200, {"role": "guest"}
        if body and "role" in body:
            return body_role_status, {}
        return 200, {"role": role_in_response or "guest",
                     "context_excerpts": [{"source": "memo.txt", "department": "general",
                                           "clearance": excerpt_clearance}]}
    return call


def test_clean_role_passes(monkeypatch):
    monkeypatch.setattr(smoke, "call", _fake_call())
    assert smoke.check_role("http://x", "guest", "k") == []


def test_leaked_excerpt_is_reported(monkeypatch):
    monkeypatch.setattr(smoke, "call", _fake_call(excerpt_clearance="confidential"))
    problems = smoke.check_role("http://x", "guest", "k")
    assert problems and all("LEAK" in p for p in problems)


def test_role_escalation_and_body_role_are_reported(monkeypatch):
    monkeypatch.setattr(smoke, "call", _fake_call(role_in_response="exec", body_role_status=200))
    problems = smoke.check_role("http://x", "guest", "k")
    assert any("X-Dev-Role must be ignored" in p for p in problems)
    assert any("body role not rejected" in p for p in problems)
