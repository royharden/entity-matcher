"""Explicit dimensional parsing: unknown quantities never become equality."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

PROFILE = "product-quantity-v1"
US_FL_OZ_ML = Decimal("29.5735295625")
UNITS = {
    "ml": ("volume", Decimal("1")),
    "milliliter": ("volume", Decimal("1")),
    "milliliters": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
    "liter": ("volume", Decimal("1000")),
    "liters": ("volume", Decimal("1000")),
    "g": ("mass", Decimal("1")),
    "gram": ("mass", Decimal("1")),
    "grams": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "mg": ("mass", Decimal("0.001")),
    "lb": ("mass", Decimal("453.59237")),
    "lbs": ("mass", Decimal("453.59237")),
    "count": ("count", Decimal("1")),
    "ct": ("count", Decimal("1")),
    "each": ("count", Decimal("1")),
}
NUMBER = r"(?:[0-9]{1,12}(?:\.[0-9]{1,12})?|\.[0-9]{1,12})"
EXPRESSION = re.compile(
    rf"^(?:(?P<pack>[0-9]{{1,6}})\s*[x×]\s*)?(?P<value>{NUMBER})\s*(?P<unit>[a-z .]+)?$"
)


@dataclass(frozen=True)
class Quantity:
    status: Literal["known", "unknown", "ambiguous", "invalid"]
    dimension: str | None
    per_pack_base: Decimal | None
    pack_count: int | None
    source_size: str
    source_unit: str
    policy: str = PROFILE + ":us-fl-oz"
    reason: str | None = None

    @property
    def total_base(self) -> Decimal | None:
        if self.status != "known" or self.per_pack_base is None or self.pack_count is None:
            return None
        with localcontext() as context:
            context.prec = 40
            return self.per_pack_base * self.pack_count


def _unit_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().strip().split()).rstrip(".")


def parse_quantity(
    size: str, unit: str, *, fluid_ounce: Literal["us", "reject"] = "us"
) -> Quantity:
    """Accept a complete size expression, with optional exact integer pack prefix.

    No extraction from descriptions or guess from bare oz. Conflicting embedded/column
    units fail. US fluid ounces are explicit default policy, configurable to rejection.
    """
    if type(size) is not str or type(unit) is not str:
        raise TypeError("Quantity requires raw strings")
    if fluid_ounce not in {"us", "reject"}:
        raise ValueError("Unknown fluid ounce policy")
    policy = PROFILE + ":" + ("us-fl-oz" if fluid_ounce == "us" else "reject-fl-oz")

    def unavailable(status: str, reason: str) -> Quantity:
        return Quantity(status, None, None, None, size, unit, policy, reason)

    if not size.strip():
        return unavailable("unknown", "missing_size")
    expression = _unit_text(size)
    match = EXPRESSION.fullmatch(expression)
    if match is None:
        return unavailable("invalid", "unsupported_size_expression")
    embedded, separate = _unit_text(match["unit"] or ""), _unit_text(unit)
    selected = embedded or separate

    def lookup(value: str) -> tuple[str, Decimal] | None:
        if value in {"fl oz", "fl. oz", "fluid ounce", "fluid ounces"}:
            return ("volume", US_FL_OZ_ML) if fluid_ounce == "us" else None
        return UNITS.get(value)

    if selected == "oz":
        return unavailable("ambiguous", "bare_ounce_requires_dimension")
    if not selected:
        return unavailable("unknown", "missing_unit")
    factor = lookup(selected)
    if factor is None:
        return unavailable("ambiguous", "unsupported_or_disallowed_unit")
    if embedded and separate and lookup(embedded) != lookup(separate):
        return unavailable("invalid", "conflicting_units")
    count = int(match["pack"] or "1")
    value = Decimal(match["value"])
    if count < 1 or count > 10_000 or value <= 0:
        return unavailable("invalid", "nonpositive_or_out_of_bound_quantity")
    with localcontext() as context:
        context.prec = 40
        base = value * factor[1]
    if base > Decimal("1e12") or base < Decimal("1e-12"):
        return unavailable("invalid", "quantity_out_of_bound")
    return Quantity("known", factor[0], base, count, size, unit, policy)


def quantities_equal(left: Quantity, right: Quantity) -> bool | None:
    """None means unknown; exact pack counts and symmetric max-relative 2% tolerance."""
    if left.status != "known" or right.status != "known":
        return None
    if left.dimension != right.dimension or left.pack_count != right.pack_count:
        return False
    a, b = left.per_pack_base, right.per_pack_base
    if a is None or b is None:
        return None
    with localcontext() as context:
        context.prec = 40
        return abs(a - b) <= Decimal("0.02") * max(a, b)
