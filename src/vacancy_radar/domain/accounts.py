"""Rules for account emails and passwords. Pure: no I/O.

No email is ever sent, so an email is only a login name here. It is checked
for a plausible shape rather than deliverability, which would need DNS.

Passwords follow NIST SP 800-63B: a minimum length and no composition rules.
The maximum only bounds the work an attacker can make the hasher do.
"""

from typing import Final

MAX_EMAIL_LENGTH: Final = 254  # RFC 5321 limit for a forward path
MIN_PASSWORD_LENGTH: Final = 8
MAX_PASSWORD_LENGTH: Final = 128

INVALID_EMAIL: Final = "Enter a valid email, such as name@example.com."


def normalize_email(raw: str) -> str:
    """The form in which an email is stored and looked up.

    Lower-casing the local part is not strictly RFC 5321, but no mail
    provider in practice treats `Ivan@` and `ivan@` as different people, and
    signing in should not depend on how the address was capitalized.
    """
    return raw.strip().lower()


def email_error(email: str) -> str | None:
    """Why a normalized email cannot be used, or None when it can."""
    if not email:
        return "Enter your email."
    if len(email) > MAX_EMAIL_LENGTH:
        return f"An email can be at most {MAX_EMAIL_LENGTH} characters."
    local, at, domain = email.rpartition("@")
    if not at or not local or any(char.isspace() for char in email):
        return INVALID_EMAIL
    labels = domain.split(".")
    if len(labels) < 2 or not all(labels):
        return INVALID_EMAIL
    return None


def password_error(password: str) -> str | None:
    """Why a new password cannot be used, or None when it can."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    return None
