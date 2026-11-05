import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only (the app does not support trio)."""
    return "asyncio"
