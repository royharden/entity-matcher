"""Validate exact decoded raw CSV fields before returning any accepted records."""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
from dataclasses import dataclass

from pydantic import ValidationError

from ..shared.match.identity import MAX_CELL_BYTES, RAW_FIELDS
from ..shared.match.models import RawProduct, RecordIdentity
from ..shared.match.normalize import parse_price

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 10_000
OVERRIDE_ENCODINGS = {"utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"}


@dataclass(frozen=True)
class InputIssue:
    code: str
    source_row: int | None
    field: str | None = None


@dataclass(frozen=True)
class CatalogValidation:
    dataset_id: str
    source_sha256: str
    encoding: str | None
    data_rows_seen: int
    row_count_complete: bool
    issues: tuple[InputIssue, ...]
    records: tuple[RawProduct, ...]

    @property
    def accepted(self) -> bool:
        return not self.issues


def _decode(data: bytes, override: str | None) -> tuple[str, str]:
    bom = (
        "utf-8-sig"
        if data.startswith(codecs.BOM_UTF8)
        else "utf-16"
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
        else None
    )
    if override is not None and override not in OVERRIDE_ENCODINGS:
        raise ValueError("unsupported_encoding")
    if bom is not None and override is not None:
        if override != bom and not (bom == "utf-8-sig" and override == "utf-8"):
            raise ValueError("encoding_conflict")
    selected = bom or override or "utf-8"
    try:
        return data.decode(selected, errors="strict"), selected
    except UnicodeError as error:
        raise ValueError("encoding_invalid_or_override_required") from error


def validate_catalog(
    data: bytes, dataset_id: str, *, encoding: str | None = None
) -> CatalogValidation:
    """No accepted rows escape on any finding; findings contain locations, never raw values.

    Source row is the physical ending line of each CSV record (including quoted newlines).
    Above the row bound, rows_seen is an observed lower bound, explicitly incomplete.
    """
    if type(data) is not bytes:
        raise TypeError("Catalog input requires exact bytes")
    source_hash = hashlib.sha256(data).hexdigest()
    issues: list[InputIssue] = []
    rows: list[RawProduct] = []
    seen: set[str] = set()
    rows_seen, complete, selected = 0, True, None
    try:
        RecordIdentity(dataset_id=dataset_id, record_id="validation-probe")
    except ValidationError:
        issues.append(InputIssue("invalid_dataset_id", None))
    if len(data) > MAX_FILE_BYTES:
        issues.append(InputIssue("file_too_large", None))
    if issues:
        return CatalogValidation(dataset_id, source_hash, None, 0, False, tuple(issues), ())
    try:
        text, selected = _decode(data, encoding)
    except ValueError as error:
        return CatalogValidation(
            dataset_id, source_hash, None, 0, False, (InputIssue(str(error), None),), ()
        )
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            issues.append(InputIssue("missing_header", 1))
            complete = False
        elif tuple(header) != RAW_FIELDS:
            code = "duplicate_header" if len(set(header)) != len(header) else "invalid_header"
            issues.append(InputIssue(code, 1))
            complete = False
        else:
            for fields in reader:
                rows_seen += 1
                line = reader.line_num
                if rows_seen > MAX_ROWS:
                    issues.append(InputIssue("too_many_rows", line))
                    complete = False
                    break
                if len(fields) != len(RAW_FIELDS):
                    issues.append(InputIssue("invalid_column_count", line))
                    continue
                row = dict(zip(RAW_FIELDS, fields, strict=True))
                start = len(issues)
                for key, value in row.items():
                    if "\x00" in value:
                        issues.append(InputIssue("nul_in_cell", line, key))
                    if len(value.encode("utf-8")) > MAX_CELL_BYTES:
                        issues.append(InputIssue("cell_too_large", line, key))
                for key in ("id", "name"):
                    if not row[key] or (key == "name" and not row[key].strip()):
                        issues.append(InputIssue("required_cell_empty", line, key))
                if row["id"] in seen:
                    issues.append(InputIssue("duplicate_id", line, "id"))
                seen.add(row["id"])
                try:
                    parse_price(row["price"])
                except ValueError:
                    issues.append(InputIssue("invalid_price", line, "price"))
                if len(issues) == start:
                    try:
                        rows.append(RawProduct(**row))
                    except ValidationError:
                        issues.append(InputIssue("invalid_raw_record", line))
    except csv.Error:
        issues.append(InputIssue("malformed_csv", reader.line_num))
        complete = False
    return CatalogValidation(
        dataset_id,
        source_hash,
        selected,
        rows_seen,
        complete,
        tuple(issues),
        () if issues else tuple(rows),
    )
