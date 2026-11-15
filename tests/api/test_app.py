import json
from collections.abc import Iterator

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from pydantic import AnyHttpUrl

from vacancy_radar.api.app import create_app
from vacancy_radar.config import Settings, get_settings

API_DOCS = ["/docs", "/redoc", "/openapi.json"]


@pytest.fixture
def default_structlog() -> Iterator[None]:
    """Start and end with structlog's defaults, whatever earlier tests configured."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


def production_settings() -> Settings:
    return get_settings().model_copy(
        update={"app_env": "production", "public_base_url": AnyHttpUrl("https://radar.example")}
    )


@pytest.mark.usefixtures("default_structlog")
def test_creating_the_app_configures_json_logging(capsys: pytest.CaptureFixture[str]) -> None:
    create_app()

    structlog.get_logger().info("telegram.unlinked", via="site")

    captured = capsys.readouterr()
    record = json.loads(captured.err)
    assert record["event"] == "telegram.unlinked"
    assert record["via"] == "site"
    assert captured.out == ""


@pytest.mark.anyio
@pytest.mark.parametrize("path", API_DOCS)
async def test_api_docs_are_served_locally(path: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        response = await c.get(path)

    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("path", API_DOCS)
async def test_api_docs_are_off_in_production(path: str) -> None:
    app = create_app(production_settings())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        response = await c.get(path)

    assert response.status_code == 404


@pytest.mark.anyio
async def test_pages_load_the_site_stylesheet_instead_of_inline_styles(
    client: AsyncClient,
) -> None:
    page = await client.get("/login")
    stylesheet = await client.get("/static/site.css")

    assert '<link rel="stylesheet" href="/static/site.css">' in page.text
    assert "<style" not in page.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "form.inline" in stylesheet.text
