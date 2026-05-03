from __future__ import annotations

from math import isfinite
from typing import Any


def parse_number(value: Any, *, default: float | None = None) -> float | None:
    """
    Parse ints/floats and common string formats safely.

    Supported examples:
    - 1234
    - 1,234
    - 1.234,5
    - 1,234.5
    - 2,24
    """

    if value is None or isinstance(value, bool):
        return default

    if isinstance(value, (int, float)):
        number = float(value)
        return number if isfinite(number) else default

    text = str(value).strip().replace(" ", "")

    if text == "":
        return default

    normalized = _normalize_number_text(text)

    try:
        number = float(normalized)
    except ValueError:
        return default

    return number if isfinite(number) else default


def parse_int(value: Any, *, default: int | None = None) -> int | None:
    number = parse_number(value)

    if number is None:
        return default

    return int(round(number))


def _normalize_number_text(text: str) -> str:
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            return text.replace(".", "").replace(",", ".")

        return text.replace(",", "")

    if "," not in text:
        return text

    parts = text.split(",")

    if len(parts) > 2:
        return "".join(parts)

    whole, fraction = parts

    if fraction == "":
        return whole

    if len(fraction) == 3 and whole.lstrip("+-").isdigit():
        return f"{whole}{fraction}"

    return f"{whole}.{fraction}"
