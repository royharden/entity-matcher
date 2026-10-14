"""Hand-computed unit/pack and independent normalization/alias controls."""

from decimal import Decimal

import pytest

from entity_matcher.shared.match.aliases import AliasError, load_aliases
from entity_matcher.shared.match.models import RawProduct
from entity_matcher.shared.match.normalize import normalize_record, normalize_text, parse_price
from entity_matcher.shared.match.quantity import parse_quantity, quantities_equal


def product(**changes: str) -> RawProduct:
    return RawProduct(
        **{
            "id": " A:1 ",
            "name": "CAFÉ—Bottle",
            "brand": "A.C.M.E",
            "size": "500",
            "unit": "ml",
            "price": "1.00",
            "category": "",
            "description": "",
        }
        | changes
    )


@pytest.mark.parametrize(
    "size,unit,dimension,base,pack",
    [
        ("500", "mL", "volume", "500", 1),
        ("0.5 L", "", "volume", "500", 1),
        ("6 × 500 ml", "", "volume", "500", 6),
        (".5", "kg", "mass", "500", 1),
        ("2", "lb", "mass", "907.18474", 1),
        ("8", "fl oz", "volume", "236.5882365000", 1),
        ("3", "ct", "count", "3", 1),
    ],
)
def test_quantity_code_matches_independent_base_oracles(
    size: str, unit: str, dimension: str, base: str, pack: int
) -> None:
    """Catches dimensional conversion and pack multipliers conflated with per-pack values."""
    q = parse_quantity(size, unit)
    assert q.status == "known" and q.dimension == dimension
    assert q.per_pack_base == Decimal(base) and q.pack_count == pack
    assert q.total_base == Decimal(base) * pack
    assert q.source_size == size and q.source_unit == unit


@pytest.mark.parametrize(
    "size,unit,status",
    [
        ("", "ml", "unknown"),
        ("500", "", "unknown"),
        ("8", "oz", "ambiguous"),
        ("500 ml", "g", "invalid"),
        ("0", "ml", "invalid"),
        ("-1", "g", "invalid"),
        ("0 x 500 ml", "", "invalid"),
        ("1/2", "L", "invalid"),
        ("500-600", "ml", "invalid"),
        ("NaN", "ml", "invalid"),
    ],
)
def test_unknown_ambiguous_and_bad_quantity_never_equals_itself(
    size: str, unit: str, status: str
) -> None:
    """Catches unknown-vs-unknown, ambiguous ounces or bad arithmetic becoming equality."""
    q = parse_quantity(size, unit)
    assert q.status == status and quantities_equal(q, q) is None
    assert q.total_base is None


def test_us_fluid_ounce_policy_can_be_rejected() -> None:
    """Catches an implicit fluid-ounce convention replacing the explicit US policy."""
    q = parse_quantity("8", "fl oz", fluid_ounce="reject")
    assert q.status == "ambiguous" and q.policy.endswith("reject-fl-oz")


def test_exact_pack_count_dimension_and_two_percent_boundary() -> None:
    """Catches equal total volume hiding six packages versus one and asymmetric tolerance drift."""
    assert quantities_equal(parse_quantity("500", "ml"), parse_quantity("0.5", "L")) is True
    assert quantities_equal(parse_quantity("6 x 500 ml", ""), parse_quantity("3", "L")) is False
    assert quantities_equal(parse_quantity("500", "ml"), parse_quantity("500", "g")) is False
    small, large = parse_quantity("490", "ml"), parse_quantity("500", "ml")
    assert quantities_equal(small, large) is True and quantities_equal(large, small) is True
    assert quantities_equal(parse_quantity("489.999", "ml"), large) is False


def test_normalization_changes_metadata_only_and_preserves_raw_identity() -> None:
    """Catches casefold/punctuation/aliases entering raw ID, fingerprints or generations."""
    source = product()
    aliases = load_aliases(b"alias,canonical\na c m e,acme\n")
    record = normalize_record(source, "catalog", aliases=aliases)
    assert record.name == "café bottle" and record.brand == "acme"
    assert record.ref.record_id == " A:1 " and record.ref.content_hash == source.fingerprint
    assert record.raw == source and record.price == Decimal("1.00")
    assert record.category == "" and record.alias_digest == aliases.digest
    assert normalize_text("é") != normalize_text("e")


def test_token_truncation_is_explicit_and_price_stays_metadata() -> None:
    """Catches hidden token truncation and nonfinite/negative prices leaking into metadata."""
    record = normalize_record(product(name=" ".join(f"word{i}" for i in range(65))), "a")
    assert len(record.name_tokens) == 64 and record.tokens_truncated
    assert parse_price("") is None and parse_price(" 0.00 ") == Decimal("0.00")
    for bad in ("-1", "NaN", "1e2"):
        with pytest.raises(ValueError):
            parse_price(bad)


def test_alias_chain_order_independent_digest_and_immutable_mapping() -> None:
    """Catches order-dependent alias resolution or mutable provenance after normalization."""
    a = load_aliases(b"alias,canonical\na,b\nb,c\n")
    b = load_aliases(b"alias,canonical\nb,c\na,b\n")
    assert a.resolve("A") == "c" and a.digest == b.digest
    with pytest.raises(TypeError):
        a.mapping["a"] = "other"
    assert load_aliases(b"alias,canonical\n").resolve(" Raw Brand ") == "raw brand"


@pytest.mark.parametrize(
    "data",
    [
        b"wrong,header\n",
        b"alias,canonical\na,b\nA,c\n",
        b"alias,canonical\na,b\nb,a\n",
        b"alias,canonical\na,A\n",
        b"alias,canonical\n,b\n",
        b'alias,canonical\na,"unterminated',
    ],
)
def test_alias_conflict_cycles_shape_and_encoding_fail(data: bytes) -> None:
    """Catches normalized conflicts, self/indirect cycles and malformed alias CSV."""
    with pytest.raises(AliasError):
        load_aliases(data)
