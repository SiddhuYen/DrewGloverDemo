"""ollama_classify: no-op when unavailable, label validation, caching. No network."""
from app import config
from app.providers import ollama_classify


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _reset(monkeypatch, *, active: bool, get_ok: bool = True):
    """Force ollama_available()'s memoized cache to re-evaluate, and stub the
    daemon reachability check independent of confidence-check tests below."""
    monkeypatch.setattr(ollama_classify, "_availability_cache", None)
    monkeypatch.setattr(config, "OLLAMA_CLASSIFY_RELATIONS", active)
    if get_ok:
        monkeypatch.setattr(ollama_classify.httpx, "get", lambda *a, **k: _FakeResp(200))
    else:
        def _raise(*a, **k):
            raise ConnectionError("no daemon")
        monkeypatch.setattr(ollama_classify.httpx, "get", _raise)


# --- vocabulary --------------------------------------------------------------
def test_allowed_vocabulary_excludes_non_groundable_types():
    """The label set is informational only — it must never include the types
    Rule 0 doesn't let free text create or the tier this feature can't verify."""
    assert "co_mention" not in ollama_classify._ALLOWED
    assert "cooccurrence" not in ollama_classify._ALLOWED
    assert "org_membership" not in ollama_classify._ALLOWED
    assert "unknown" in ollama_classify._ALLOWED
    assert "cofounder" in ollama_classify._ALLOWED


# --- no-op paths ---------------------------------------------------------
def test_classify_is_a_noop_when_disabled(monkeypatch):
    _reset(monkeypatch, active=False)
    out = ollama_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "text"}])
    assert out == [{"label": "unknown", "confidence": 0.0}]


def test_classify_is_a_noop_when_daemon_unreachable(monkeypatch):
    _reset(monkeypatch, active=True, get_ok=False)
    out = ollama_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "text"}])
    assert out == [{"label": "unknown", "confidence": 0.0}]


def test_classify_of_empty_items_is_a_noop(monkeypatch):
    _reset(monkeypatch, active=True)
    assert ollama_classify.classify([]) == []


def test_classify_skips_items_with_no_evidence(monkeypatch):
    """No evidence text means nothing to judge — never sent to the model."""
    _reset(monkeypatch, active=True)
    calls = {"n": 0}
    monkeypatch.setattr(ollama_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.cache, "set", lambda *a, **k: None)

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp(200, {"response": "{}"})
    monkeypatch.setattr(ollama_classify.httpx, "post", fake_post)

    out = ollama_classify.classify([{"a": "Alice", "b": "Bob", "evidence": ""}])
    assert out == [{"label": "unknown", "confidence": 0.0}]
    assert calls["n"] == 0


# --- happy path + validation ----------------------------------------------
def test_classify_happy_path(monkeypatch):
    _reset(monkeypatch, active=True)
    monkeypatch.setattr(ollama_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.httpx, "post", lambda *a, **k: _FakeResp(
        200, {"response": '{"1": {"label": "cofounder", "confidence": 0.9}}'}))

    out = ollama_classify.classify(
        [{"a": "Alice", "b": "Bob", "evidence": "co-founded together"}])
    assert out == [{"label": "cofounder", "confidence": 0.9}]


def test_classify_rejects_a_label_outside_the_allowed_vocabulary(monkeypatch):
    """The model can echo back anything; a label outside the controlled
    vocabulary must fall back to unknown rather than being trusted verbatim."""
    _reset(monkeypatch, active=True)
    monkeypatch.setattr(ollama_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.httpx, "post", lambda *a, **k: _FakeResp(
        200, {"response": '{"1": {"label": "best_friend", "confidence": 0.9}}'}))

    out = ollama_classify.classify(
        [{"a": "Alice", "b": "Bob", "evidence": "best friends since college"}])
    assert out == [{"label": "unknown", "confidence": 0.9}]


def test_classify_clamps_confidence_to_unit_range(monkeypatch):
    _reset(monkeypatch, active=True)
    monkeypatch.setattr(ollama_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.httpx, "post", lambda *a, **k: _FakeResp(
        200, {"response": '{"1": {"label": "colleague", "confidence": 5}}'}))

    out = ollama_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "worked together"}])
    assert out[0]["confidence"] == 1.0


def test_classify_a_malformed_response_yields_unknown(monkeypatch):
    _reset(monkeypatch, active=True)
    monkeypatch.setattr(ollama_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.httpx, "post",
                        lambda *a, **k: _FakeResp(200, {"response": "not json"}))

    out = ollama_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "text"}])
    assert out == [{"label": "unknown", "confidence": 0.0}]


def test_classify_a_500_yields_unknown(monkeypatch):
    _reset(monkeypatch, active=True)
    monkeypatch.setattr(ollama_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(ollama_classify.httpx, "post", lambda *a, **k: _FakeResp(500))

    out = ollama_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "text"}])
    assert out == [{"label": "unknown", "confidence": 0.0}]


# --- caching -----------------------------------------------------------------
def test_classify_caches_by_evidence_so_a_repeat_call_skips_the_network(monkeypatch):
    _reset(monkeypatch, active=True)
    store = {}
    monkeypatch.setattr(ollama_classify.cache, "get",
                        lambda key, track=True: store.get(key))
    monkeypatch.setattr(ollama_classify.cache, "set",
                        lambda key, kind, value, ttl: store.__setitem__(key, value))
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp(200, {"response": '{"1": {"label": "colleague", "confidence": 0.6}}'})
    monkeypatch.setattr(ollama_classify.httpx, "post", fake_post)

    item = {"a": "Alice", "b": "Bob", "evidence": "worked together at Acme"}
    first = ollama_classify.classify([item])
    second = ollama_classify.classify([item])
    assert first == second == [{"label": "colleague", "confidence": 0.6}]
    assert calls["n"] == 1
