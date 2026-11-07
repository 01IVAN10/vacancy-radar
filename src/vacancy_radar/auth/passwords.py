"""Password hashing with Argon2 (pwdlib's recommended hasher).

Hashing is deliberately slow and CPU-bound, so async code calls these
functions through a thread pool instead of on the event loop.
"""

from functools import lru_cache

from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """An Argon2 hash in PHC string format, salt and parameters included."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Whether the password matches, and a new hash when the stored one is outdated.

    The new hash is returned when the hash was made with older parameters
    than the current ones, so it can be replaced on a successful sign-in.
    """
    return _hasher.verify_and_update(password, password_hash)


def verify_unknown_account(password: str) -> None:
    """Spend the time of a real check when no account has the given email.

    Without this, signing in with an unknown email would answer faster than
    with a wrong password. Sign-up already tells whether an email has an
    account (see `docs/auth.md`), so this only keeps sign-in from becoming a
    second, quieter way to ask, and it stays right once sign-up stops telling.
    """
    _hasher.verify(password, _dummy_hash())


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("not a password of any account")
