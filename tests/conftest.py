"""
conftest.py - Set test DB env var before any modules are imported.
"""

import os

import pytest

# Single test DB path used by all test files
TEST_DB = "/tmp/leadclaw_test.db"
os.environ["LEADCLAW_DB"] = TEST_DB


def _remove_db():
    for suffix in ("", "-shm", "-wal"):
        path = TEST_DB + suffix
        if os.path.exists(path):
            os.remove(path)


@pytest.fixture(autouse=True)
def fresh_db():
    from leadclaw import db

    _remove_db()
    db.init_db()
    yield
    _remove_db()
