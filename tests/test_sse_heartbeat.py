"""The SSE stream must keep the connection warm during a long, silent
enrichment step, or a proxy / Codespaces port-forward / the browser drops it
mid-deep-search ("connection lost"). We drive _sse with a work function that
stays silent past the heartbeat interval and assert a keepalive comment is sent
before the final result.
"""
import asyncio
import time

import app.main as main


class _DummyDB:
    def close(self):
        pass


async def _collect(resp):
    out = []
    async for chunk in resp.body_iterator:
        out.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(out)


def _drain(monkeypatch, work, heartbeat=0.05):
    monkeypatch.setattr(main.config, "SSE_HEARTBEAT_S", heartbeat)
    monkeypatch.setattr(main, "SessionLocal", lambda: _DummyDB())
    resp = main._sse(work)
    return asyncio.run(_collect(resp))


def test_heartbeat_is_sent_during_a_silent_step(monkeypatch):
    def work(db, progress):
        time.sleep(0.25)                 # a long step that emits no progress
        return {"connected": True}

    body = _drain(monkeypatch, work)

    assert ": keepalive" in body         # the connection was kept warm
    assert "event: result" in body       # and the result still arrived
    assert '"connected": true' in body


def test_progress_and_result_still_flow(monkeypatch):
    def work(db, progress):
        progress("step one")
        progress("step two")
        return {"connected": False, "reason": "none"}

    body = _drain(monkeypatch, work, heartbeat=5.0)  # no silent gap here

    assert "event: progress" in body
    assert "step one" in body and "step two" in body
    assert "event: result" in body


def test_worker_exception_becomes_a_result_not_a_dropped_stream(monkeypatch):
    def work(db, progress):
        raise RuntimeError("boom")

    body = _drain(monkeypatch, work, heartbeat=5.0)

    # An error is reported as a normal result event, so the stream closes
    # cleanly instead of erroring out the EventSource.
    assert "event: result" in body
    assert "boom" in body
