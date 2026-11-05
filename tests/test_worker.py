from vacancy_radar.worker import celery_app, ping


def test_ping_returns_pong() -> None:
    # apply() runs the task in-process, so no broker is needed.
    assert ping.apply().get() == "pong"


def test_beat_schedule_is_empty_until_real_tasks_exist() -> None:
    assert celery_app.conf.beat_schedule == {}
