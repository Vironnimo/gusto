"""Conservative, exact scaling of leading ingredient quantities."""
from __future__ import annotations

import re
from fractions import Fraction


_UNICODE_FRACTIONS = {
    "½": Fraction(1, 2), "¼": Fraction(1, 4), "¾": Fraction(3, 4),
    "⅓": Fraction(1, 3), "⅔": Fraction(2, 3), "⅛": Fraction(1, 8),
    "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8),
    "⅕": Fraction(1, 5), "⅖": Fraction(2, 5), "⅗": Fraction(3, 5),
    "⅘": Fraction(4, 5), "⅙": Fraction(1, 6), "⅚": Fraction(5, 6),
}
_FRACTIONS = "".join(_UNICODE_FRACTIONS)
_NUMBER = re.compile(
    rf"(?:\d+\s+\d+/\d+|\d+\s*[{_FRACTIONS}]|\d+/\d+|"
    rf"\d+(?:[,.]\d+)?|[{_FRACTIONS}])"
)
_RANGE = re.compile(r"^(\s*(?:-|–|—|bis\b)\s*)", re.IGNORECASE)
_WARNING = "Menge konnte nicht sicher skaliert werden; bitte prüfen."
_UNQUANTIFIED = "Ohne eindeutige Mengenangabe unverändert übernommen; bitte prüfen."


def _number(text: str) -> Fraction:
    if text[-1:] in _UNICODE_FRACTIONS:
        return Fraction(int(text[:-1].strip() or "0")) + _UNICODE_FRACTIONS[text[-1]]
    parts = text.split()
    if len(parts) == 2 and "/" in parts[1]:
        whole, fraction = parts
        return Fraction(int(whole)) + Fraction(fraction)
    return Fraction(text.replace(",", "."))


def _format(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    denominator = value.denominator
    while denominator % 2 == 0:
        denominator //= 2
    while denominator % 5 == 0:
        denominator //= 5
    if denominator == 1:
        # Integer arithmetic retains every significant digit; do not round.
        scale, remainder = 1, value.denominator
        while remainder != 1:
            if remainder % 2 == 0:
                remainder //= 2
            else:
                remainder //= 5
            scale *= 10
        integer = value.numerator * scale // value.denominator
        decimal = f"{integer // scale},{integer % scale:0{len(str(scale)) - 1}d}"
        return decimal.rstrip("0").rstrip(",")
    whole, rest = divmod(value.numerator, value.denominator)
    fraction = f"{rest}/{value.denominator}"
    return f"{whole} {fraction}" if whole else fraction


def scale_ingredient(text: str, factor: Fraction) -> dict:
    """Scale only a clear leading quantity or range; never round a quantity."""
    result = {"original": text, "scaled": text, "warning": None}
    if factor == 1:
        return result
    first = _NUMBER.match(text)
    if not first:
        result["warning"] = (_WARNING if re.search(rf"\d|[{_FRACTIONS}]", text)
                             else _UNQUANTIFIED)
        return result

    rest = text[first.end():]
    range_mark = _RANGE.match(rest)
    second = _NUMBER.match(rest, range_mark.end()) if range_mark else None
    if range_mark and not second:
        result["warning"] = _WARNING
        return result
    tail = rest[second.end():] if second else rest
    # Additional numbers, arithmetic and malformed quantity syntax can mean
    # package sizes or several ingredients. Percentages and pack descriptors
    # describe the product, not the amount to buy. A dot with three trailing
    # digits may be either a decimal point or a German thousands separator.
    ambiguous_dot = r"[1-9]\d{0,2}\.\d{3}"
    if (re.fullmatch(ambiguous_dot, first.group())
            or (second and re.fullmatch(ambiguous_dot, second.group()))
            or re.match(r"\s*(?:%|er(?:\b|[-–]))", tail, re.IGNORECASE)
            or re.search(rf"\d|[{_FRACTIONS}]|[+×]", tail)
            or re.match(r"\s*[xX](?:\s|\d)", tail)
            or re.match(r"\s*[/.,–—-]", tail)):
        result["warning"] = _WARNING
        return result
    try:
        scaled = _format(_number(first.group()) * factor)
        if second:
            scaled += range_mark.group() + _format(_number(second.group()) * factor)
    except (ValueError, ZeroDivisionError):
        result["warning"] = _WARNING
        return result
    result["scaled"] = scaled + tail
    return result
