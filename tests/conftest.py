"""Shared PostgreSQL test fixtures."""

from __future__ import annotations

import os

import asyncpg
import pytest

from data.store import SCHEMA_STATEMENTS

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL_TEST", "postgresql://localhost/agentic_investment_test"
)


@pytest.fixture
async def db():
    """Connect to the test database, run schema in a transaction, then rollback."""
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    tr = conn.transaction()
    await tr.start()
    for stmt in SCHEMA_STATEMENTS:
        await conn.execute(stmt)
    yield conn
    await tr.rollback()
    await conn.close()
