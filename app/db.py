"""SQLAlchemy engine / session wiring.

Single local SQLite graph (demo-only, one fixed start node). WAL mode + a busy
timeout so readers never block the writer and brief contention retries.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

Base = declarative_base()


def _tune_sqlite(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def _make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):
        event.listen(eng, "connect", _tune_sqlite)
    return eng


engine = _make_engine(config.DB_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            future=True)


def init_db(bind=None) -> None:
    """Create all tables (idempotent), then apply additive migrations."""
    from . import models  # noqa: F401  (register mappers)

    target = bind or engine
    Base.metadata.create_all(bind=target)
    _migrate(target)


def _migrate(bind) -> None:
    """Additive column migrations for pre-existing SQLite files (create_all
    won't ALTER an existing table). Each is a guarded no-op when applied."""
    add_columns = [
        ("people", "wikidata_qid", "TEXT"),
        ("people", "wikidata_sitelinks", "INTEGER DEFAULT 0"),
        ("people", "is_warm", "INTEGER DEFAULT 0"),
        ("people", "enriched", "INTEGER DEFAULT 0"),
        ("organizations", "member_count", "INTEGER DEFAULT 0"),
        ("relationship_edges", "metadata", "JSON"),
    ]
    with bind.begin() as conn:
        for table, col, coltype in add_columns:
            try:
                cols = {r[1] for r in conn.exec_driver_sql(
                    f"PRAGMA table_info({table})").fetchall()}
                if col not in cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # non-SQLite or already present — safe to ignore


def get_db():
    """FastAPI dependency yielding a session on the default engine."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
