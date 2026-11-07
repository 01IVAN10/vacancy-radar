from argon2 import PasswordHasher

from tests.support import PASSWORD
from vacancy_radar.auth.passwords import hash_password, verify_password, verify_unknown_account


def test_hash_is_argon2_and_salted() -> None:
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)

    assert first.startswith("$argon2id$")
    assert first != second


def test_verify_accepts_the_right_password_only(password_hash: str) -> None:
    assert verify_password(PASSWORD, password_hash) == (True, None)
    assert verify_password("wrong password", password_hash) == (False, None)


def test_verify_returns_a_new_hash_for_outdated_parameters() -> None:
    # Argon2 parameters weaker than pwdlib's current defaults (t=2 instead of 3).
    old_hash = PasswordHasher(time_cost=2).hash(PASSWORD)

    matches, new_hash = verify_password(PASSWORD, old_hash)

    assert matches
    assert new_hash is not None
    assert verify_password(PASSWORD, new_hash) == (True, None)


def test_verify_unknown_account_does_not_raise() -> None:
    verify_unknown_account(PASSWORD)
