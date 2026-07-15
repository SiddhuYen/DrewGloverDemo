"""PyInstaller entry point.

A top-level script, not `app/desktop.py` run directly: as `__main__` that module
would sit outside its package and its relative imports would fail.
"""
from app.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
