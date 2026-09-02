"""Shared test setup.

The application keeps three process-wide caches — repository retrieval, the
analysis cache and interview sessions. They are what make a real session usable
(one repository is touched several times, and a local analysis costs minutes),
but a cache that survives between tests turns them into order-dependent flakes:
a test that mocks a 404 would be served the previous test's successful result.

Clearing them before every test is the only isolation this suite needs; nothing
else here holds state.
"""

from __future__ import annotations

import pytest

from app.services.github.service import reset_retrieval_cache
from app.services.interview.store import reset_stores


@pytest.fixture(autouse=True)
def _isolated_caches():
    """Give every test empty caches, and leave none behind."""
    reset_retrieval_cache()
    reset_stores()
    yield
    reset_retrieval_cache()
    reset_stores()
