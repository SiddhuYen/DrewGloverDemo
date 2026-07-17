"""The two properties that make a bring-your-own-key web deployment safe.

Both were true for free in the desktop build (one user, one baked key) and
become load-bearing the moment the app is served to more than one person.
"""
from fastapi.testclient import TestClient

from app import config, session
from app.main import app
from app.providers import llm_classify


def _client() -> TestClient:
    # Each TestClient keeps its own cookie jar, which is what makes it stand in
    # for a separate visitor.
    return TestClient(app)


def test_key_is_never_returned_to_the_browser():
    c = _client()
    c.post("/claude-key", json={"claude_key": "sk-ant-SECRET-VALUE"})

    body = c.get("/settings").text
    assert "SECRET-VALUE" not in body
    assert c.get("/settings").json()["claude_configured"] is True


def test_key_is_not_readable_from_the_session_cookie():
    c = _client()
    c.post("/claude-key", json={"claude_key": "sk-ant-SECRET-VALUE"})

    jar = "".join(f"{k}={v}" for k, v in c.cookies.items())
    assert "SECRET-VALUE" not in jar


def test_one_visitors_key_does_not_leak_to_another(monkeypatch):
    # No server-side fallback, so anything the second visitor sees came from
    # the first one's session rather than the environment.
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "")

    alice = _client()
    alice.post("/claude-key", json={"claude_key": "sk-ant-ALICE"})
    assert alice.get("/settings").json()["claude_configured"] is True

    bob = _client()
    assert bob.get("/settings").json()["claude_configured"] is False


def test_server_key_is_the_fallback_when_the_visitor_has_none(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "sk-ant-SERVER")
    assert _client().get("/settings").json()["claude_configured"] is True


def test_client_cache_does_not_hand_one_key_to_the_next_caller(monkeypatch):
    """The pre-web client cache was a single global — it would have served
    whoever warmed it first to every caller after."""
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "")
    llm_classify._clients.clear()

    token = session.bind(session.new_session())
    session.set_claude_key("sk-ant-ALICE")
    alice_client = llm_classify._get_client()
    session.reset(token)

    token = session.bind(session.new_session())
    session.set_claude_key("sk-ant-BOB")
    bob_client = llm_classify._get_client()
    session.reset(token)

    assert alice_client is not bob_client
    assert alice_client.api_key == "sk-ant-ALICE"
    assert bob_client.api_key == "sk-ant-BOB"


def test_cookie_is_secure_on_https_but_still_works_on_localhost():
    """Both halves matter: forcing Secure on would silently break the documented
    local run (a Secure cookie is not sent over http), and leaving it off would
    ship the session id in the clear in production."""
    assert config.cookie_secure_for("https") is True
    assert config.cookie_secure_for("http") is False


def test_expired_session_stops_resolving_its_key(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_API_KEY", "")
    monkeypatch.setattr(session, "SESSION_TTL_S", -1)  # already stale on write

    token = session.bind(session.new_session())
    session.set_claude_key("sk-ant-ALICE")
    assert session.current_claude_key() == ""
    session.reset(token)
