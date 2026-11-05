import json
from collections.abc import Iterator

import pytest
import structlog

from vacancy_radar.log import configure_logging


@pytest.fixture(autouse=True)
def reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def test_json_output_has_level_timestamp_and_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json=True)

    structlog.get_logger().info("collect.finished", source="djinni", inserted=3)

    record = json.loads(capsys.readouterr().err)
    assert record["event"] == "collect.finished"
    assert record["level"] == "info"
    assert record["source"] == "djinni"
    assert record["inserted"] == 3
    assert record["timestamp"].endswith("Z")


def test_messages_below_the_level_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", json=True)

    structlog.get_logger().info("hidden")
    structlog.get_logger().warning("shown")

    assert [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()] == ["shown"]


def test_console_output_is_human_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json=False)

    structlog.get_logger().info("collect.finished", source="djinni")

    err = capsys.readouterr().err
    assert "collect.finished" in err
    assert "source" in err
    with pytest.raises(json.JSONDecodeError):
        json.loads(err)
