import pytest
import redis
import respx

from vacancy_radar.sources.http import build_client
from vacancy_radar.sources.ratelimit import KEY_PREFIX, RateLimitError, TokenBucket, wait_for_token


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def bucket(redis_client: redis.Redis, clock: FakeClock) -> TokenBucket:
    return TokenBucket(redis_client, rate=1.0, capacity=1, clock=clock)


def test_first_request_gets_a_token_at_once(bucket: TokenBucket) -> None:
    assert bucket.acquire("djinni.co") == 0


def test_next_request_in_the_same_second_must_wait(bucket: TokenBucket, clock: FakeClock) -> None:
    bucket.acquire("djinni.co")
    clock.advance(0.25)

    assert bucket.acquire("djinni.co") == pytest.approx(0.75)


def test_token_is_back_after_one_second(bucket: TokenBucket, clock: FakeClock) -> None:
    bucket.acquire("djinni.co")
    clock.advance(1.0)

    assert bucket.acquire("djinni.co") == 0


def test_idle_time_does_not_build_up_beyond_the_capacity(
    bucket: TokenBucket, clock: FakeClock
) -> None:
    bucket.acquire("djinni.co")
    clock.advance(60)

    assert bucket.acquire("djinni.co") == 0
    assert bucket.acquire("djinni.co") == pytest.approx(1.0)


def test_capacity_allows_a_burst(redis_client: redis.Redis, clock: FakeClock) -> None:
    bucket = TokenBucket(redis_client, rate=1.0, capacity=3, clock=clock)

    assert [bucket.acquire("djinni.co") for _ in range(4)] == [0, 0, 0, pytest.approx(1.0)]


def test_domains_have_separate_buckets(bucket: TokenBucket) -> None:
    bucket.acquire("djinni.co")

    assert bucket.acquire("jobs.dou.ua") == 0


def test_buckets_are_shared_between_limiter_instances(
    redis_client: redis.Redis, clock: FakeClock
) -> None:
    # Two worker processes, each with its own TokenBucket, share one limit.
    TokenBucket(redis_client, clock=clock).acquire("djinni.co")

    assert TokenBucket(redis_client, clock=clock).acquire("djinni.co") > 0


def test_bucket_key_expires(bucket: TokenBucket, redis_client: redis.Redis) -> None:
    bucket.acquire("djinni.co")

    ttl = redis_client.pttl(KEY_PREFIX + "djinni.co")
    assert 0 < ttl <= 2000


def test_wait_for_token_sleeps_until_a_token_is_free(bucket: TokenBucket, clock: FakeClock) -> None:
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    wait_for_token(bucket, "djinni.co", sleep=sleep)
    wait_for_token(bucket, "djinni.co", sleep=sleep)

    assert sleeps == [pytest.approx(1.0)]


def test_wait_for_token_gives_up_after_max_wait(bucket: TokenBucket) -> None:
    bucket.acquire("djinni.co")

    with pytest.raises(RateLimitError, match=r"djinni\.co"):
        wait_for_token(bucket, "djinni.co", max_wait=0.5, sleep=lambda _: None)


def test_client_with_a_rate_limiter_takes_a_token_per_host(
    respx_mock: respx.MockRouter, bucket: TokenBucket, clock: FakeClock
) -> None:
    respx_mock.get("https://djinni.co/jobs/rss/").respond(200)

    with build_client(rate_limiter=bucket) as client:
        client.get("https://djinni.co/jobs/rss/")
        # The token for djinni.co is taken, so the next one would have to wait.
        assert bucket.acquire("djinni.co") > 0


def test_client_does_not_send_a_request_it_has_no_token_for(
    respx_mock: respx.MockRouter, redis_client: redis.Redis, clock: FakeClock
) -> None:
    route = respx_mock.get("https://djinni.co/jobs/rss/").respond(200)
    # A rate so low that the second request would wait far beyond MAX_WAIT_SECONDS.
    bucket = TokenBucket(redis_client, rate=0.001, capacity=1, clock=clock)

    with build_client(rate_limiter=bucket) as client:
        client.get("https://djinni.co/jobs/rss/")
        with pytest.raises(RateLimitError):
            client.get("https://djinni.co/jobs/rss/")

    assert route.call_count == 1


def test_client_without_a_rate_limiter_has_no_hooks() -> None:
    with build_client() as client:
        assert client.event_hooks["request"] == []
