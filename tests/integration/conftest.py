"""Integration test fixtures: real Kafka + Postgres via testcontainers.

The fixtures override the environment *before* B2's settings module is
imported so ``shared.config.settings`` reads the container endpoints. Run with
``make test-integration`` (Docker daemon required).
"""

from __future__ import annotations

import os

import pytest

try:
    from testcontainers.kafka import KafkaContainer
    from testcontainers.postgres import PostgresContainer
except ImportError:  # pragma: no cover
    pytest.skip("testcontainers not installed", allow_module_level=True)

try:
    import docker
except ImportError:  # pragma: no cover
    pytest.skip("docker SDK not installed", allow_module_level=True)


def _docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


if not _docker_available():  # pragma: no cover
    pytest.skip("Docker daemon not available; skipping integration tests", allow_module_level=True)


@pytest.fixture(scope="session")
def kafka_container():
    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as kafka:
        os.environ["KAFKA_BROKERS"] = kafka.get_bootstrap_server()
        yield kafka


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        os.environ["POSTGRES_URL"] = url
        yield pg


@pytest.fixture(scope="session")
def fresh_db(postgres_container):
    """Create tables via SQLAlchemy metadata. Faster than alembic for tests.

    The migrations are exercised separately by ``test_migrations`` if needed.
    """
    # Lazy-import after env is set so settings pick up the container URL.
    import importlib

    import shared.config as cfg

    importlib.reload(cfg)
    import shared.db as db_mod

    importlib.reload(db_mod)
    import shared.models as models_mod

    importlib.reload(models_mod)
    models_mod.Base.metadata.create_all(bind=db_mod.engine)
    yield db_mod
