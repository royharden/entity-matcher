"""Exact versioned identity preimages; no parsing, normalization or ruling authority."""

from __future__ import annotations

import hashlib
import json
import re

RAW_FIELDS = ("id", "name", "brand", "size", "unit", "price", "category", "description")
MAX_CELL_BYTES = 8192
MAX_DATASET_ID_BYTES = 256
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class IdentityError(ValueError):
    """Invalid identity material; messages exclude raw input values."""


def _string(value: object, *, maximum: int, nonempty: bool = False) -> str:
    if type(value) is not str:
        raise IdentityError("Expected an exact string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as error:
        raise IdentityError("Invalid Unicode identity material") from error
    if size > maximum or (nonempty and not value):
        raise IdentityError("String length outside declared bounds")
    return value


def _closed_strings(data: object, fields: tuple[str, ...]) -> dict[str, str]:
    if type(data) is not dict or set(data) != set(fields):
        raise IdentityError("Unexpected or missing identity fields")
    return {field: _string(data[field], maximum=MAX_CELL_BYTES) for field in fields}


def canonical_bytes(data: object) -> bytes:
    """canon/1 serializer for validated JSON; callers validate the identity shape."""
    try:
        return json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise IdentityError("Invalid canonical JSON material") from error


def record_identity(data: object) -> dict[str, str]:
    result = _closed_strings(data, ("dataset_id", "record_id"))
    _string(result["dataset_id"], maximum=MAX_DATASET_ID_BYTES, nonempty=True)
    _string(result["record_id"], maximum=MAX_CELL_BYTES, nonempty=True)
    return result


def identity_from_ref(data: object) -> dict[str, str]:
    """Explicitly project a content-bearing ref onto stable identity fields only."""
    result = _closed_strings(data, ("dataset_id", "record_id", "content_hash"))
    digest(result["content_hash"])
    return record_identity({field: result[field] for field in ("dataset_id", "record_id")})


def raw_fields(data: object) -> dict[str, str]:
    result = _closed_strings(data, RAW_FIELDS)
    _string(result["id"], maximum=MAX_CELL_BYTES, nonempty=True)
    _string(result["name"], maximum=MAX_CELL_BYTES, nonempty=True)
    return result


def digest(value: object) -> str:
    if type(value) is not str or SHA256_PATTERN.fullmatch(value) is None:
        raise IdentityError("Expected a full lowercase SHA256 digest")
    return value


def raw_record_preimage(raw: object) -> bytes:
    return canonical_bytes({"algorithm": "product-raw-fields-v1", "fields": raw_fields(raw)})


def raw_record_fingerprint(raw: object) -> str:
    return hashlib.sha256(raw_record_preimage(raw)).hexdigest()


def raw_record_generation(raw: object) -> str:
    return "rg1:" + raw_record_fingerprint(raw)


def _ordered(left: object, right: object) -> tuple[dict[str, str], dict[str, str], bool]:
    a, b = record_identity(left), record_identity(right)
    a_bytes, b_bytes = canonical_bytes(a), canonical_bytes(b)
    if a_bytes == b_bytes:
        raise IdentityError("A pair requires two distinct record identities")
    return (a, b, False) if a_bytes < b_bytes else (b, a, True)


def pair_key_preimage(left: object, right: object) -> bytes:
    low, high, _ = _ordered(left, right)
    return canonical_bytes({"algorithm_version": 1, "kind": "pair", "refs": [low, high]})


def pair_key(left: object, right: object) -> str:
    return "pk1:" + hashlib.sha256(pair_key_preimage(left, right)).hexdigest()


def pair_generation_preimage(
    left: object, left_fingerprint: object, right: object, right_fingerprint: object
) -> bytes:
    _, _, swapped = _ordered(left, right)
    fingerprints = [digest(left_fingerprint), digest(right_fingerprint)]
    if swapped:
        fingerprints.reverse()
    return canonical_bytes(
        {
            "algorithm_version": 1,
            "pair_key": pair_key(left, right),
            "raw_fingerprints": fingerprints,
        }
    )


def pair_generation(
    left: object, left_fingerprint: object, right: object, right_fingerprint: object
) -> str:
    preimage = pair_generation_preimage(left, left_fingerprint, right, right_fingerprint)
    return "pg1:" + hashlib.sha256(preimage).hexdigest()


def field_evidence_preimage(identity: object, raw: object, field_id: object) -> bytes | None:
    ref, fields = record_identity(identity), raw_fields(raw)
    if ref["record_id"] != fields["id"]:
        raise IdentityError("Record identity disagrees with raw record")
    if type(field_id) is not str or field_id not in RAW_FIELDS:
        raise IdentityError("Unknown raw field")
    value = fields[field_id]
    if not value:
        return None
    fingerprint = raw_record_fingerprint(fields)
    return canonical_bytes(
        {
            "algorithm": "product-record-field-evidence-v1",
            "dataset_id": ref["dataset_id"],
            "record_id": ref["record_id"],
            "source_sha256": fingerprint,
            "source_revision": "rg1:" + fingerprint,
            "field_id": field_id,
            "raw_value": value,
        }
    )


def field_evidence_digest(identity: object, raw: object, field_id: object) -> str | None:
    preimage = field_evidence_preimage(identity, raw, field_id)
    return None if preimage is None else hashlib.sha256(preimage).hexdigest()


def field_excerpt_digest(raw: object, field_id: object) -> str | None:
    fields = raw_fields(raw)
    if type(field_id) is not str or field_id not in RAW_FIELDS:
        raise IdentityError("Unknown raw field")
    value = fields[field_id]
    return None if not value else hashlib.sha256(value.encode("utf-8")).hexdigest()
