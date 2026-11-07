"""Narrowing stored text to the fixed vocabularies the domain types. Pure: no I/O."""


def one_of[T: str](value: str, allowed: tuple[T, ...]) -> T:
    """`value` typed as one of `allowed`. Raises `ValueError` for anything else.

    Rows read from the database hold plain `str`. Passing them through here
    gives them their `Literal` type without a `cast` that would hide a bad value.
    """
    for candidate in allowed:
        if value == candidate:
            return candidate
    raise ValueError(f"{value!r} is not one of {allowed}")
