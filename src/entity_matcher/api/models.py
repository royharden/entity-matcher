"""Closed upload wire with strict bounded JSON and exact original-byte decoding."""

import base64
import binascii
import json
import re
from typing import Annotated, Literal
from urllib.parse import parse_qsl

from pydantic import Field, field_validator

from ..io.catalog import MAX_FILE_BYTES
from ..shared.match.aliases import MAX_ALIAS_BYTES
from ..shared.match.models import FrozenContract, RecordIdentity


class ComparisonInputError(ValueError):
    """A fixed reason code; never forward parser/Pydantic errors containing input."""


class ComparisonUpload(FrozenContract):
    schema_version: Literal[1]
    dataset_a: Annotated[str, Field(min_length=1, max_length=256)]
    dataset_b: Annotated[str, Field(min_length=1, max_length=256)]
    catalog_a_base64: Annotated[str, Field(max_length=MAX_FILE_BYTES, repr=False)]
    catalog_b_base64: Annotated[str, Field(max_length=MAX_FILE_BYTES, repr=False)]
    aliases_base64: Annotated[str | None, Field(max_length=2 * MAX_ALIAS_BYTES, repr=False)] = None
    encoding_a: Literal["utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"] | None = None
    encoding_b: Literal["utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"] | None = None
    method: Literal["rules", "fuzzy"] = "rules"

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("integer_version_required")
        return value

    @field_validator("dataset_a", "dataset_b")
    @classmethod
    def exact_namespace(cls, value):
        RecordIdentity(dataset_id=value, record_id="namespace-validation")
        return value


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ComparisonInputError("duplicate_json_key")
        result[key] = value
    return result


def _no_constant(value):
    raise ComparisonInputError("nonfinite_json_number")


def parse_upload(body: bytes) -> ComparisonUpload:
    if type(body) is not bytes or len(body) > MAX_FILE_BYTES:
        raise ComparisonInputError("upload_body_bound")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ComparisonInputError("upload_utf8_required") from error
    if text.startswith("\ufeff"):
        raise ComparisonInputError("upload_json_bom_unsupported")
    # Bound parser structure BEFORE allocating arbitrary JSON containers. The
    # actual upload schema is flat, with large content confined to strings.
    depth = separators = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 4:
                raise ComparisonInputError("upload_json_depth_bound")
        elif char in "]}":
            depth -= 1
        elif char in ",:":
            separators += 1
            if separators > 64:
                raise ComparisonInputError("upload_json_structure_bound")
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
        return ComparisonUpload.model_validate(value)
    except ComparisonInputError:
        raise
    except (ValueError, TypeError, RecursionError) as error:
        raise ComparisonInputError("invalid_comparison_upload") from error


def _decode(value, maximum):
    try:
        # validate=True alone accepts noncanonical padding bits; enforce the
        # exact standard alphabet/padding representation by a round trip.
        data = base64.b64decode(value, validate=True)
        if len(data) > maximum or base64.b64encode(data).decode("ascii") != value:
            raise ComparisonInputError("noncanonical_or_oversized_base64")
        return data
    except ComparisonInputError:
        raise
    except (ValueError, TypeError, binascii.Error) as error:
        raise ComparisonInputError("invalid_upload_base64") from error


def decode_catalog_bytes(upload: ComparisonUpload) -> tuple[bytes, bytes, bytes | None]:
    try:
        upload = ComparisonUpload.model_validate(upload)
    except (ValueError, TypeError) as error:
        raise ComparisonInputError("invalid_comparison_upload") from error
    if upload.dataset_a == upload.dataset_b:
        raise ComparisonInputError("dataset_namespaces_must_differ")
    left = _decode(upload.catalog_a_base64, MAX_FILE_BYTES)
    right = _decode(upload.catalog_b_base64, MAX_FILE_BYTES)
    aliases = (
        _decode(upload.aliases_base64, MAX_ALIAS_BYTES)
        if upload.aliases_base64 is not None
        else None
    )
    if len(left) + len(right) + (len(aliases) if aliases is not None else 0) > MAX_FILE_BYTES:
        raise ComparisonInputError("combined_raw_upload_bound")
    return left, right, aliases


def parse_page_query(raw: bytes) -> dict:
    if type(raw) is not bytes or len(raw) > 1024:
        raise ComparisonInputError("page_query_bound")
    try:
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=3,
        )
        values = _no_duplicates(pairs)
        if not values.keys() <= {"offset", "limit", "method"}:
            raise ValueError("unknown")
        method = values.get("method", "rules")
        if method not in {"rules", "fuzzy"}:
            raise ValueError("method")
        for key in ("offset", "limit"):
            if key in values and re.fullmatch(r"[0-9]{1,6}", values[key]) is None:
                raise ValueError("integer")
        offset, limit = int(values.get("offset", "0")), int(values.get("limit", "50"))
        if not 0 <= offset <= 500_000 or not 1 <= limit <= 100:
            raise ValueError("bound")
        return {"method": method, "offset": offset, "limit": limit}
    except (ValueError, TypeError, UnicodeError) as error:
        raise ComparisonInputError("invalid_page_query") from error
