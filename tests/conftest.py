"""
conftest.py - Set test DB env var before any modules are imported.
"""

import os

# Single test DB path used by all test files
TEST_DB = "/tmp/leadclaw_test.db"
os.environ["LEADCLAW_DB"] = TEST_DB
