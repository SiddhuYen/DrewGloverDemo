"""Make the console able to print the output this app actually produces.

Every rendered path is drawn with box characters and markers - `--`, `|`, a dot
for a hop, a star for a warm contact. On Windows the console is cp1252 by
default, which contains none of them, so `python -m app.cli connect ...` died
with UnicodeEncodeError before printing a single route. That is a crash on the
happy path, not a cosmetic issue.

Two independent fixes, because either alone leaves a hole:

  * the code page controls what the console can RENDER (mojibake without it),
  * the stream encoding controls what Python is willing to WRITE (the crash).

`errors="replace"` is the backstop: a console we failed to switch prints `?`
instead of taking the process down.
"""
from __future__ import annotations

import sys


def enable_utf8() -> None:
    """Idempotent; safe to call on any platform."""
    if sys.platform == "win32":
        try:
            import ctypes

            # 65001 = UTF-8. Output only: we never read from the console.
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass  # a redirected or absent console has no code page to set

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Not a reconfigurable TextIOWrapper (pytest's capture, a pipe some
            # harness replaced). Nothing to do, and nothing worth failing over.
            pass
