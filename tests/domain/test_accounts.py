import pytest

from vacancy_radar.domain.accounts import (
    MAX_EMAIL_LENGTH,
    email_error,
    normalize_email,
    password_error,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ivan@example.com", "ivan@example.com"),
        ("  Ivan@Example.COM \n", "ivan@example.com"),
    ],
)
def test_normalize_email_trims_and_lowercases(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "email",
    [
        "ivan@example.com",
        "ivan.b+jobs@mail.example.co.uk",
        # The local part may itself contain "@" when quoted; the domain is after the last one.
        '"ivan@home"@example.com',
    ],
)
def test_accepts_plausible_emails(email: str) -> None:
    assert email_error(email) is None


@pytest.mark.parametrize(
    ("email", "message"),
    [
        ("", "Enter your email."),
        ("ivan", "valid email"),
        ("@example.com", "valid email"),
        ("ivan@", "valid email"),
        ("ivan@localhost", "valid email"),
        ("ivan@example.", "valid email"),
        ("ivan@.example.com", "valid email"),
        ("ivan b@example.com", "valid email"),
        ("i" * MAX_EMAIL_LENGTH + "@example.com", "at most 254"),
    ],
)
def test_rejects_implausible_emails(email: str, message: str) -> None:
    error = email_error(email)

    assert error is not None
    assert message in error


@pytest.mark.parametrize("password", ["12345678", "x" * 128, "пароль з пробілами"])
def test_accepts_passwords_of_allowed_length(password: str) -> None:
    assert password_error(password) is None


@pytest.mark.parametrize(
    ("password", "message"),
    [("", "at least 8"), ("1234567", "at least 8"), ("x" * 129, "at most 128")],
)
def test_rejects_passwords_too_short_or_too_long(password: str, message: str) -> None:
    error = password_error(password)

    assert error is not None
    assert message in error
