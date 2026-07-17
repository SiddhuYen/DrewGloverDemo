"""Per-visitor Claude credentials, held server-side.

The desktop build had one trusted user and one baked-in key (see DESKTOP.md).
On the web that model breaks in two ways, and this module exists for both.

First, a module-global key is cross-tenant: the first visitor to paste one
would silently spend it on behalf of every later visitor. So a key lives in a
store keyed by an opaque session id, and `current_claude_key()` resolves it
per request — a key set by one visitor is invisible to another even though
both run through the same process.

Second, a key that reaches the browser is a key that leaks. Only the session
id travels, in an HttpOnly cookie, so page scripts cannot read it either; the
key itself is never serialised into any response. `/settings` reports whether
a key is configured, never what it is.

The store is in-process and deliberately not persisted: a restart should drop
every visitor's credential rather than leave keys on disk (which is what the
Serper path does, and why that one stays desktop-shaped).
"""
from __future__ import annotations

import secrets
import threading
import time
from contextvars import ContextVar
from typing import Dict, Optional

from . import config

COOKIE_NAME = "vcwi_session"

# Idle sessions are swept rather than kept: an abandoned tab should not hold a
# live credential indefinitely.
SESSION_TTL_S = 12 * 3600

# Hard ceiling so an unauthenticated flood of cookie-less requests cannot grow
# the store without bound. Oldest-expiring entries are dropped first.
_MAX_SESSIONS = 5000

_lock = threading.Lock()
_store: Dict[str, dict] = {}

# Set by the middleware on every request; read by the provider layer far below
# it. A ContextVar (not a global) is what keeps concurrent visitors isolated —
# Starlette copies the context into the threadpool that runs sync endpoints, so
# this survives the hop into `def` handlers.
_current: ContextVar[Optional[str]] = ContextVar("vcwi_session_id", default=None)


def _sweep_locked() -> None:
    now = time.monotonic()
    for sid in [s for s, v in _store.items() if v["expires"] <= now]:
        del _store[sid]
    if len(_store) > _MAX_SESSIONS:
        oldest = sorted(_store, key=lambda s: _store[s]["expires"])
        for sid in oldest[: len(_store) - _MAX_SESSIONS]:
            del _store[sid]


def new_session() -> str:
    sid = secrets.token_urlsafe(32)
    with _lock:
        _sweep_locked()
        _store[sid] = {"claude_key": "", "expires": time.monotonic() + SESSION_TTL_S}
    return sid


def touch(sid: Optional[str]) -> bool:
    """Extend a session's life. False when the id is unknown or expired."""
    if not sid:
        return False
    with _lock:
        entry = _store.get(sid)
        if entry is None or entry["expires"] <= time.monotonic():
            return False
        entry["expires"] = time.monotonic() + SESSION_TTL_S
        return True


def bind(sid: Optional[str]):
    """Make `sid` the session for the current request context."""
    return _current.set(sid)


def reset(token) -> None:
    """Undo a bind(). Paired with bind() in a finally so a worker thread never
    carries one visitor's session into the next request it happens to serve."""
    _current.reset(token)


def set_claude_key(key: str) -> bool:
    """Store this visitor's key. False when there is no live session to put it in."""
    sid = _current.get()
    if not sid:
        return False
    with _lock:
        entry = _store.get(sid)
        if entry is None:
            return False
        entry["claude_key"] = (key or "").strip()
        return True


def current_claude_key() -> str:
    """The key for this request: the visitor's own, else the server's own.

    The env-var fallback (`config.CLAUDE_API_KEY`) is what keeps the CLI, the
    tests, and a single-operator deployment working unchanged — there is no
    request context in those, so there is no session to read.
    """
    sid = _current.get()
    if sid:
        with _lock:
            entry = _store.get(sid)
            if entry and entry["expires"] > time.monotonic() and entry["claude_key"]:
                return entry["claude_key"]
    return config.CLAUDE_API_KEY


def claude_configured() -> bool:
    return bool(current_claude_key())
