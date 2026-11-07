from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from tests.support import TEST_SECRET_KEY
from vacancy_radar.auth.tokens import TOKEN_TTL, issue_token, read_token, signing_key
from vacancy_radar.config import Settings

DATABASE_URL = "postgresql+psycopg://user:secret@db.example:5432/vacancy_radar"


def settings(secret_key: str | None) -> Settings:
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,  # type: ignore[arg-type]
        secret_key=None if secret_key is None else SecretStr(secret_key),
    )


def test_token_round_trip() -> None:
    token = issue_token(42, key=TEST_SECRET_KEY)

    assert read_token(token, key=TEST_SECRET_KEY) == 42


def test_token_expires_after_ttl() -> None:
    now = datetime.now(UTC)
    fresh = issue_token(42, key=TEST_SECRET_KEY, now=now - TOKEN_TTL + timedelta(minutes=1))
    expired = issue_token(42, key=TEST_SECRET_KEY, now=now - TOKEN_TTL - timedelta(minutes=1))

    assert read_token(fresh, key=TEST_SECRET_KEY) == 42
    assert read_token(expired, key=TEST_SECRET_KEY) is None


def test_token_signed_with_another_key_is_rejected() -> None:
    token = issue_token(42, key="another-secret-key-of-the-same-length-x")

    assert read_token(token, key=TEST_SECRET_KEY) is None


def test_token_with_tampered_payload_is_rejected() -> None:
    header, _payload, signature = issue_token(42, key=TEST_SECRET_KEY).split(".")
    forged_payload = jwt.utils.base64url_encode(b'{"sub":"1","iat":0,"exp":9999999999}').decode()

    assert read_token(f"{header}.{forged_payload}.{signature}", key=TEST_SECRET_KEY) is None


def test_unsigned_token_is_rejected() -> None:
    now = datetime.now(UTC)
    claims = {"sub": "42", "iat": now, "exp": now + TOKEN_TTL}
    unsigned = jwt.encode(claims, key=None, algorithm="none")

    assert read_token(unsigned, key=TEST_SECRET_KEY) is None


@pytest.mark.parametrize(
    "claims",
    [
        {"iat": 0, "exp": 9999999999},
        {"sub": "42", "exp": 9999999999},
        {"sub": "42", "iat": 0},
        {"sub": "forty-two", "iat": 0, "exp": 9999999999},
        {"sub": "-1", "iat": 0, "exp": 9999999999},
    ],
)
def test_token_without_a_valid_subject_or_times_is_rejected(claims: dict[str, object]) -> None:
    token = jwt.encode(claims, TEST_SECRET_KEY, algorithm="HS256")

    assert read_token(token, key=TEST_SECRET_KEY) is None


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_garbage_is_rejected(token: str) -> None:
    assert read_token(token, key=TEST_SECRET_KEY) is None


def test_signing_key_requires_a_long_enough_secret() -> None:
    assert signing_key(settings(TEST_SECRET_KEY)) == TEST_SECRET_KEY
    with pytest.raises(RuntimeError, match="SECRET_KEY is required"):
        signing_key(settings(None))
    with pytest.raises(RuntimeError, match="at least 32"):
        signing_key(settings("x" * 31))
