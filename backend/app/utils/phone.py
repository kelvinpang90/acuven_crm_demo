"""Phone number normalization (docs/chatbot-events.md sections 3 and 9.3).

One definition for every place that stores or matches a contact's phone:
contact create / edit / sheet import, the AutoCount customer sync, inbound
matching and the stored-number migration. Stored shape is digits only, no `+`,
with the Malaysian country code, e.g. `60123456789`.
"""

import re
from typing import Optional

_NON_DIGITS = re.compile(r"\D")

MIN_DIGITS = 8
MAX_DIGITS = 15


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Return the normalized number, or None when it cannot be normalized.

    1. Drop everything that is not a digit (a leading `+` included).
    2. Drop a leading `00` (international dialling prefix) from those digits.
    3. Exactly one leading `0` (Malaysian local form) becomes `60`.
    4. Still starting with `0`, or not 8-15 digits: cannot be normalized.

    The result never starts with `0`, so normalizing it again changes nothing.
    """
    if raw is None:
        return None
    digits = _NON_DIGITS.sub("", str(raw))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and not digits.startswith("00"):
        digits = "60" + digits[1:]
    if digits.startswith("0") or not MIN_DIGITS <= len(digits) <= MAX_DIGITS:
        return None
    return digits


def phone_for_storage(raw: Optional[str]) -> Optional[str]:
    """What a save path writes: the normalized number, or the input unchanged
    when it cannot be normalized (blank and None included)."""
    normalized = normalize_phone(raw)
    return normalized if normalized is not None else raw
