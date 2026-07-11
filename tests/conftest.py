"""In-memory graph fixtures. No network, no cache, no spaCy."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           future=True)
    import app.models  # noqa: F401  (register mappers)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                           future=True)()
    try:
        yield session
    finally:
        session.close()
