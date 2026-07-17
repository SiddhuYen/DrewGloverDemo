"""llm_classify: no-op when unavailable, label validation, caching. No network —
the Anthropic client is monkeypatched out entirely."""
import types

from app import config
from app.providers import llm_classify


def _fake_client(parse_fn):
    return types.SimpleNamespace(messages=types.SimpleNamespace(parse=parse_fn))


def _fake_response(pairs):
    """pairs: [(label, confidence), ...] -> a fake messages.parse() response."""
    items = [llm_classify._LabelItem(label=label, confidence=conf) for label, conf in pairs]
    return types.SimpleNamespace(parsed_output=llm_classify._LabelResult(results=items))


def _activate(monkeypatch, *, enabled: bool = True, api_key: str = "sk-test"):
    """Direct-to-Anthropic mode: CLAUDE_API_BASE unset, CLAUDE_API_KEY set —
    what a local dev run without a proxy looks like."""
    monkeypatch.setattr(config, "LLM_CLASSIFY_ENABLED", enabled)
    monkeypatch.setattr(config, "CLAUDE_API_BASE", None)
    monkeypatch.setattr(config, "CLAUDE_API_KEY", api_key)
    monkeypatch.setattr(config, "LITELLM_VIRTUAL_KEY", "")


# --- vocabulary --------------------------------------------------------------
def test_allowed_vocabulary_excludes_non_groundable_types():
    """The label set is informational only — it must never include the types
    Rule 0 doesn't let free text create or the tier this feature can't verify."""
    assert "co_mention" not in llm_classify._ALLOWED
    assert "cooccurrence" not in llm_classify._ALLOWED
    assert "org_membership" not in llm_classify._ALLOWED
    assert "unknown" in llm_classify._ALLOWED
    assert "cofounder" in llm_classify._ALLOWED


# --- which key gets used (direct vs. proxied) -------------------------------
def test_active_key_is_the_real_key_when_no_proxy_base_is_set(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_API_BASE", None)
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "sk-real-direct")
    monkeypatch.setattr(config, "LITELLM_VIRTUAL_KEY", "")
    assert llm_classify._active_api_key() == "sk-real-direct"


def test_active_key_is_the_virtual_key_when_a_proxy_base_is_set(monkeypatch):
    """A real key sitting in the environment must never leak to a proxy
    that isn't actually Anthropic — proxied mode uses ONLY the virtual key,
    even if a real key also happens to be set."""
    monkeypatch.setattr(config, "CLAUDE_API_BASE", "https://proxy.example.fly.dev")
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "sk-real-should-be-ignored")
    monkeypatch.setattr(config, "LITELLM_VIRTUAL_KEY", "sk-virtual")
    assert llm_classify._active_api_key() == "sk-virtual"


def test_available_is_false_with_a_proxy_base_but_no_virtual_key(monkeypatch):
    """Having a real Anthropic key set doesn't count once we're routed
    through a proxy — only the virtual key does."""
    monkeypatch.setattr(config, "CLAUDE_API_BASE", "https://proxy.example.fly.dev")
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "sk-real-but-irrelevant")
    monkeypatch.setattr(config, "LITELLM_VIRTUAL_KEY", "")
    assert llm_classify.llm_available() is False


# --- availability --------------------------------------------------------
def test_llm_available_reflects_api_key_presence(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_API_BASE", None)
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "")
    assert llm_classify.llm_available() is False
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "sk-test")
    assert llm_classify.llm_available() is True


def test_is_active_requires_both_the_flag_and_a_key(monkeypatch):
    _activate(monkeypatch, enabled=False, api_key="sk-test")
    assert llm_classify.is_active() is False
    _activate(monkeypatch, enabled=True, api_key="")
    assert llm_classify.is_active() is False
    _activate(monkeypatch, enabled=True, api_key="sk-test")
    assert llm_classify.is_active() is True


# --- no-op paths ---------------------------------------------------------
def test_classify_is_a_noop_when_disabled(monkeypatch):
    _activate(monkeypatch, enabled=False)
    out = llm_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "text"}])
    assert out == [{"label": "unknown", "confidence": 0.0}]


def test_classify_is_a_noop_without_an_api_key(monkeypatch):
    _activate(monkeypatch, api_key="")
    out = llm_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "text"}])
    assert out == [{"label": "unknown", "confidence": 0.0}]


def test_classify_of_empty_items_is_a_noop(monkeypatch):
    _activate(monkeypatch)
    assert llm_classify.classify([]) == []


def test_classify_skips_items_with_no_evidence(monkeypatch):
    """No evidence text means nothing to judge — never sent to the model."""
    _activate(monkeypatch)
    monkeypatch.setattr(llm_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify.cache, "set", lambda *a, **k: None)
    calls = {"n": 0}

    def fake_parse(**kwargs):
        calls["n"] += 1
        return _fake_response([])
    monkeypatch.setattr(llm_classify, "_get_client", lambda: _fake_client(fake_parse))

    out = llm_classify.classify([{"a": "Alice", "b": "Bob", "evidence": ""}])
    assert out == [{"label": "unknown", "confidence": 0.0}]
    assert calls["n"] == 0


# --- happy path + validation ----------------------------------------------
def test_classify_happy_path(monkeypatch):
    _activate(monkeypatch)
    monkeypatch.setattr(llm_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify, "_get_client",
                        lambda: _fake_client(lambda **kw: _fake_response([("cofounder", 0.9)])))

    out = llm_classify.classify(
        [{"a": "Alice", "b": "Bob", "evidence": "co-founded together"}])
    assert out == [{"label": "cofounder", "confidence": 0.9}]


def test_classify_rejects_a_label_outside_the_allowed_vocabulary(monkeypatch):
    """The model can echo back anything; a label outside the controlled
    vocabulary must fall back to unknown rather than being trusted verbatim."""
    _activate(monkeypatch)
    monkeypatch.setattr(llm_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify, "_get_client",
                        lambda: _fake_client(lambda **kw: _fake_response([("best_friend", 0.9)])))

    out = llm_classify.classify(
        [{"a": "Alice", "b": "Bob", "evidence": "best friends since college"}])
    assert out == [{"label": "unknown", "confidence": 0.9}]


def test_classify_clamps_confidence_to_unit_range(monkeypatch):
    _activate(monkeypatch)
    monkeypatch.setattr(llm_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify, "_get_client",
                        lambda: _fake_client(lambda **kw: _fake_response([("colleague", 5.0)])))

    out = llm_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "worked together"}])
    assert out[0]["confidence"] == 1.0


def test_classify_a_short_response_yields_unknown_for_the_missing_tail(monkeypatch):
    """If the model returns fewer results than items sent, the unmatched
    items fall back to unknown rather than misaligning by position."""
    _activate(monkeypatch)
    monkeypatch.setattr(llm_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify, "_get_client",
                        lambda: _fake_client(lambda **kw: _fake_response([("colleague", 0.6)])))

    out = llm_classify.classify([
        {"a": "Alice", "b": "Bob", "evidence": "worked together"},
        {"a": "Alice", "b": "Carol", "evidence": "photographed at a gala"},
    ])
    assert out == [
        {"label": "colleague", "confidence": 0.6},
        {"label": "unknown", "confidence": 0.0},
    ]


def test_classify_a_request_failure_yields_unknown(monkeypatch):
    _activate(monkeypatch)
    monkeypatch.setattr(llm_classify.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(llm_classify.cache, "set", lambda *a, **k: None)

    def raise_it(**kwargs):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(llm_classify, "_get_client", lambda: _fake_client(raise_it))

    out = llm_classify.classify([{"a": "Alice", "b": "Bob", "evidence": "text"}])
    assert out == [{"label": "unknown", "confidence": 0.0}]


# --- caching -----------------------------------------------------------------
def test_classify_caches_by_evidence_so_a_repeat_call_skips_the_network(monkeypatch):
    _activate(monkeypatch)
    store = {}
    monkeypatch.setattr(llm_classify.cache, "get",
                        lambda key, track=True: store.get(key))
    monkeypatch.setattr(llm_classify.cache, "set",
                        lambda key, kind, value, ttl: store.__setitem__(key, value))
    calls = {"n": 0}

    def fake_parse(**kwargs):
        calls["n"] += 1
        return _fake_response([("colleague", 0.6)])
    monkeypatch.setattr(llm_classify, "_get_client", lambda: _fake_client(fake_parse))

    item = {"a": "Alice", "b": "Bob", "evidence": "worked together at Acme"}
    first = llm_classify.classify([item])
    second = llm_classify.classify([item])
    assert first == second == [{"label": "colleague", "confidence": 0.6}]
    assert calls["n"] == 1
