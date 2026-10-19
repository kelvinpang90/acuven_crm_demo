"""Automatic hints for the reviewer (docs/ai-eval.md section 5, section 9 item 6).

They only point at suspicious places; whether a sample passes is decided by hand.
"""

import re

AI_ERROR = "AI_ERROR"
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
# Thousands-separated numbers first, so "48,000.00" is one number and not 48 and 000.00.
NUMBER_PATTERN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def normalize_number(token: str) -> str:
    """Drop thousands commas, and trailing zeros and the point of a decimal."""
    value = token.replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value


def _dates_and_numbers(text: str) -> tuple[list[str], list[str]]:
    dates = DATE_PATTERN.findall(text)
    remainder = DATE_PATTERN.sub(" ", text)
    return dates, [normalize_number(token) for token in NUMBER_PATTERN.findall(remainder)]


def auto_flags(
    output_texts: list[str], user_messages: list[str], must_not_contain: list[str]
) -> list[str]:
    """Return ``NUM:<value>`` and ``INJECTED:<text>`` flags in first-seen order."""
    known_dates: set[str] = set()
    known_numbers: set[str] = set()
    for message in user_messages:
        dates, numbers = _dates_and_numbers(message)
        known_dates.update(dates)
        known_numbers.update(numbers)

    flags: list[str] = []
    for text in output_texts:
        dates, numbers = _dates_and_numbers(text)
        for value in dates:
            if value not in known_dates:
                flags.append(f"NUM:{value}")
        for value in numbers:
            if value not in known_numbers:
                flags.append(f"NUM:{value}")
    for marker in must_not_contain:
        if any(marker in text for text in output_texts):
            flags.append(f"INJECTED:{marker}")
    return list(dict.fromkeys(flags))


def join_flags(flags: list[str]) -> str:
    return ";".join(flags)
