import pytest
from httpx import ASGITransport, AsyncClient

from vacancy_radar import __version__
from vacancy_radar.api.app import create_app


@pytest.mark.anyio
async def test_health_reports_ok() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
