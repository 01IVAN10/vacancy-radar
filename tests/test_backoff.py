import pytest

from vacancy_radar.backoff import exponential_backoff


@pytest.mark.parametrize(
    ("retries", "delay"), [(0, 30), (1, 60), (2, 120), (3, 240), (4, 480), (5, 600), (50, 600)]
)
def test_without_jitter_the_delay_doubles_up_to_the_cap(retries: int, delay: int) -> None:
    assert exponential_backoff(retries, base=30, cap=600, jitter=False) == delay


@pytest.mark.parametrize("retries", [0, 3, 5, 50])
def test_full_jitter_stays_between_zero_and_the_doubled_delay(retries: int) -> None:
    ceiling = min(600, 30 * 2**retries)

    delays = {exponential_backoff(retries, base=30, cap=600, jitter=True) for _ in range(200)}

    assert all(0 <= d <= ceiling for d in delays)
    # Spread out, not one fixed value.
    assert len(delays) > 1


def test_full_jitter_includes_both_ends() -> None:
    # Ceiling 2: 200 draws miss one of three values with probability ~1e-35.
    delays = {exponential_backoff(1, base=1, cap=60, jitter=True) for _ in range(200)}

    assert delays == {0, 1, 2}


def test_negative_retries_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        exponential_backoff(-1, base=1, cap=60, jitter=False)
