"""Separate product matching text/quantity metadata from immutable raw identity."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from .models import RawProduct, RecordRef
from .quantity import Quantity, parse_quantity

if TYPE_CHECKING:
    from .aliases import AliasTable

PROFILE = "product-normalize-v1"
MAX_NAME_TOKENS = 64
PRICE = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")


def normalize_text(raw: str) -> str:
    if type(raw) is not str:
        raise TypeError("Normalization requires an exact string")
    text = unicodedata.normalize("NFKC", raw).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def parse_price(raw: str) -> Decimal | None:
    """Nonnegative decimal metadata only; retain original raw representation elsewhere."""
    if type(raw) is not str:
        raise TypeError("Price requires an exact string")
    value = raw.strip()
    if not value:
        return None
    if PRICE.fullmatch(value) is None:
        raise ValueError("Invalid decimal price")
    return Decimal(value)


@dataclass(frozen=True)
class NormalizedRecord:
    raw: RawProduct
    ref: RecordRef
    name: str
    brand: str
    category: str
    description: str
    name_tokens: tuple[str, ...]
    tokens_truncated: bool
    quantity: Quantity
    price: Decimal | None
    alias_digest: str | None
    profile: str = PROFILE


def normalize_record(
    raw: RawProduct, dataset_id: str, *, aliases: AliasTable | None = None
) -> NormalizedRecord:
    accepted = RawProduct.model_validate(raw)
    name, brand = normalize_text(accepted.name), normalize_text(accepted.brand)
    if aliases is not None:
        brand = aliases.resolve(brand)
    tokens = tuple(sorted(set(name.split())))
    return NormalizedRecord(
        accepted,
        RecordRef.from_raw(dataset_id, accepted),
        name,
        brand,
        normalize_text(accepted.category),
        normalize_text(accepted.description),
        tokens[:MAX_NAME_TOKENS],
        len(tokens) > MAX_NAME_TOKENS,
        parse_quantity(accepted.size, accepted.unit),
        parse_price(accepted.price),
        aliases.digest if aliases is not None else None,
    )
