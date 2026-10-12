"""Phone number normalization (docs/chatbot-events.md sections 3 and 9.3).

AC1 of CRM-TASK-007: strip non-digits, drop a leading `00` from those digits,
exactly one leading `0` becomes `60`, and anything still starting with `0` or
outside 8-15 digits cannot be normalized. Normalizing a result again is a no-op.
"""

import pytest

from app.utils.phone import normalize_phone, phone_for_storage


@pytest.mark.parametrize(
    "raw",
    [
        "+60 12-345 6789",
        "012-345 6789",
        "0060123456789",
        "+0060123456789",
        "60123456789",
    ],
)
def test_every_written_form_gives_the_same_number(raw):
    """AC1: each example in the design normalizes to 60123456789."""
    assert normalize_phone(raw) == "60123456789"


def test_plus_then_00_is_handled_on_the_digits():
    """AC1 (section 9.3): `+00...` loses the `+` first, then the `00` -- in one pass."""
    assert normalize_phone("+0060123456789") == "60123456789"


@pytest.mark.parametrize(
    "raw",
    [
        "0000123456789",  # `00` dropped, still starts with `0`
        "1234567",  # 7 digits
        "+60 1234",  # 6 digits after stripping
        "1234567890123456",  # 16 digits
        None,
        "",
        "   ",
        "+-() ",
        "abc",
    ],
)
def test_cannot_be_normalized(raw):
    """AC1: leading `0` left over, fewer than 8 or more than 15 digits, empty or no digits."""
    assert normalize_phone(raw) is None


def test_length_bounds_are_inclusive():
    """AC1: 8 and 15 digits are valid; the bounds themselves are not rejected."""
    assert normalize_phone("12345678") == "12345678"
    assert normalize_phone("123456789012345") == "123456789012345"


def test_only_a_single_leading_zero_becomes_60():
    """AC1 (section 9.3): `0` is replaced only when exactly one leads."""
    assert normalize_phone("0123456789") == "60123456789"
    # `000...`: the first two go as the `00` prefix, the third is a single `0`.
    assert normalize_phone("000123456789") == "60123456789"


@pytest.mark.parametrize(
    "raw",
    [
        "+60 12-345 6789",
        "012-345 6789",
        "0060123456789",
        "+0060123456789",
        "60123456789",
        "000123456789",
        "12345678",
        "123456789012345",
    ],
)
def test_normalizing_twice_changes_nothing(raw):
    """AC1 (section 9.3): the result never starts with `0`, so a second pass is a no-op."""
    once = normalize_phone(raw)
    assert once is not None
    assert normalize_phone(once) == once


@pytest.mark.parametrize("raw", [None, "", "12345", "0000123456789", "ext. 12"])
def test_storage_keeps_what_cannot_be_normalized(raw):
    """AC2: save paths store the input unchanged when it cannot be normalized."""
    assert phone_for_storage(raw) == raw


def test_storage_uses_the_normalized_form():
    """AC2: save paths store the normalized number when there is one."""
    assert phone_for_storage("012-345 6789") == "60123456789"
