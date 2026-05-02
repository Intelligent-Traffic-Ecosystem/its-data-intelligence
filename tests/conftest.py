import os
import sys

import pytest

# Add src/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all database tables before running tests."""
    from shared.db import engine
    from shared.models import Base

    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
