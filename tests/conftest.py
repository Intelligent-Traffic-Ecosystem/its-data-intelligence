import os
import sys

# Add src/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.models import Base

# ---------------------------------------------------------------------------
# In-memory SQLite engine – used for all unit tests that touch the DB
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite:///:memory:"

_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def _create_tables():
    Base.metadata.create_all(bind=_engine)


_create_tables()


# ---------------------------------------------------------------------------
# Pytest fixture: override FastAPI's get_db dependency with SQLite session
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def override_get_db():
    """Replace the real postgres get_db with an in-memory SQLite session."""
    from api.main import app
    from shared.db import get_db

    def _get_test_db():
        db = _TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_test_db
    yield
    app.dependency_overrides.clear()
