"""Independent malformed-input controls; microcases contain no retail/Gold dataset."""

import codecs
import csv
import io

import pytest

from entity_matcher.io import catalog
from entity_matcher.shared.match.identity import RAW_FIELDS


def csv_bytes(rows: list[list[str]], header: list[str] | None = None) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(list(RAW_FIELDS) if header is None else header)
    writer.writerows(rows)
    return stream.getvalue().encode()


def row(id: str = "1", name: str = " Café ", price: str = "1.00") -> list[str]:
    return [id, name, "", "500", "ml", price, "", ""]


def test_valid_csv_keeps_raw_ids_blank_cells_price_and_quoted_newlines() -> None:
    """Catches raw trimming, decimal coercion and physical-line versus record accounting."""
    data = csv_bytes([row(" 1 ", " Café,\nProduct ")])
    result = catalog.validate_catalog(data, "a")
    assert result.accepted and result.encoding == "utf-8"
    assert result.data_rows_seen == 1 and result.row_count_complete
    assert result.records[0].id == " 1 " and result.records[0].name == " Café,\nProduct "
    assert result.records[0].brand == "" and result.records[0].price == "1.00"


@pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16"])
def test_bom_detection_preserves_logical_raw_generation(codec: str) -> None:
    """Catches file encoding/BOM being conflated with logical decoded record generation."""
    text = csv_bytes([row()]).decode()
    ordinary = catalog.validate_catalog(text.encode(), "a")
    encoded = catalog.validate_catalog(text.encode(codec), "a")
    assert ordinary.accepted and encoded.accepted
    assert ordinary.records[0].fingerprint == encoded.records[0].fingerprint
    assert ordinary.source_sha256 != encoded.source_sha256


def test_legacy_encoding_requires_explicit_supported_override() -> None:
    """Catches silent cp1252 fallback or lossy replacement of opaque raw text."""
    data = csv_bytes([row()]).decode().encode("cp1252")
    assert not catalog.validate_catalog(data, "a").accepted
    explicit = catalog.validate_catalog(data, "a", encoding="cp1252")
    assert explicit.accepted and explicit.records[0].name == " Café "
    assert (
        catalog.validate_catalog(data, "a", encoding="unknown").issues[0].code
        == "unsupported_encoding"
    )


def test_conflicting_bom_override_and_invalid_namespace_fail_without_rows() -> None:
    """Catches overrides defeating BOM policy or invalid namespace entering stable identity."""
    data = codecs.BOM_UTF8 + csv_bytes([row()])
    assert (
        catalog.validate_catalog(data, "a", encoding="utf-16").issues[0].code == "encoding_conflict"
    )
    assert catalog.validate_catalog(data, "a", encoding="utf-8").accepted
    assert catalog.validate_catalog(data, "").issues[0].code == "invalid_dataset_id"


@pytest.mark.parametrize(
    "header",
    [
        ["id", "id", *RAW_FIELDS[2:]],
        list(RAW_FIELDS[:-1]),
        [*RAW_FIELDS, "extra"],
        list(reversed(RAW_FIELDS)),
    ],
)
def test_closed_header_rejected_before_any_accepted_data(header: list[str]) -> None:
    """Catches missing/extra/duplicate/reordered column names being silently remapped."""
    result = catalog.validate_catalog(csv_bytes([row()], header), "a")
    assert not result.accepted and not result.records and not result.row_count_complete


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity", "1e3", "$1.00", "1,00"])
def test_invalid_decimal_prices_reject_whole_input(price: str) -> None:
    """Catches nonfinite/negative/currency/locale/exponent inputs entering price metadata."""
    result = catalog.validate_catalog(csv_bytes([row(), row("2", price=price)]), "a")
    assert result.issues == (catalog.InputIssue("invalid_price", 3, "price"),)
    assert not result.records


def test_all_observed_bad_rows_report_locations_without_values() -> None:
    """Catches partial accepted rows or raw content exposure in validation findings."""
    result = catalog.validate_catalog(
        csv_bytes([row(), row(), row("2", "   "), row("3")[:-1]]), "a"
    )
    assert [(i.code, i.source_row) for i in result.issues] == [
        ("duplicate_id", 3),
        ("required_cell_empty", 4),
        ("invalid_column_count", 5),
    ]
    assert result.data_rows_seen == 4 and not result.records


@pytest.mark.parametrize("name,code", [("é" * 4097, "cell_too_large"), ("a\x00b", "nul_in_cell")])
def test_cell_byte_bound_and_nul_controls(name: str, code: str) -> None:
    """Catches UTF8 bytes mistaken for characters and embedded NUL accepted by CSV parsing."""
    result = catalog.validate_catalog(csv_bytes([row(name=name)]), "a")
    assert result.issues[0].code == code and not result.records


def test_malformed_csv_and_empty_file_fail() -> None:
    """Catches unterminated quoted fields or absent header being accepted as an empty catalog."""
    result = catalog.validate_catalog(csv_bytes([]) + b'1,"unterminated', "a")
    assert result.issues[0].code == "malformed_csv" and not result.row_count_complete
    assert catalog.validate_catalog(b"", "a").issues[0].code == "missing_header"
    assert catalog.validate_catalog(csv_bytes([]), "a").accepted


def test_file_and_row_fences_reject_without_silent_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches file/row fences with small controls, without a production-size stress claim."""
    assert catalog.MAX_ROWS == 10_000 and catalog.MAX_FILE_BYTES == 20 * 1024 * 1024
    monkeypatch.setattr(catalog, "MAX_FILE_BYTES", 2)
    assert catalog.validate_catalog(csv_bytes([]), "a").issues[0].code == "file_too_large"
    monkeypatch.setattr(catalog, "MAX_FILE_BYTES", 1024)
    monkeypatch.setattr(catalog, "MAX_ROWS", 2)
    result = catalog.validate_catalog(csv_bytes([row("1"), row("2"), row("3")]), "a")
    assert result.issues[0].code == "too_many_rows"
    assert result.data_rows_seen == 3 and not result.row_count_complete and not result.records


def test_row_reorder_preserves_namespace_and_raw_fingerprints() -> None:
    """Catches row positions entering raw identity and cross-namespace IDs being conflated."""
    a = catalog.validate_catalog(csv_bytes([row("é"), row("e\u0301")]), "a")
    b = catalog.validate_catalog(csv_bytes([row("e\u0301"), row("é")]), "b")
    assert a.accepted and b.accepted
    assert {r.id: r.fingerprint for r in a.records} == {r.id: r.fingerprint for r in b.records}
