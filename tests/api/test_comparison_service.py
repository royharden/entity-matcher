"""Real workspace/page composition through a bounded in-memory source service."""

import base64
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from entity_matcher.api import service as module
from entity_matcher.api.models import (
    ComparisonInputError,
    ComparisonUpload,
    decode_catalog_bytes,
    parse_page_query,
    parse_upload,
)
from entity_matcher.api.service import ComparisonService, ComparisonServiceError
from entity_matcher.pipeline.review_page import (
    decode_review_page,
    encode_review_page,
    make_review_page,
)
from entity_matcher.pipeline.workspace import create_workspace


def catalog(
    record_id="one", *, name="Berry Soap", description='Plain "quoted", text\r\nsecond line'
):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(("id", "name", "brand", "size", "unit", "price", "category", "description"))
    writer.writerow((record_id, name, "Orchard", "500", "mL", "2.00", "Soap", description))
    return out.getvalue().encode("utf-8")


def body(*, left=None, right=None, **changes):
    wire = {
        "schema_version": 1,
        "dataset_a": "a",
        "dataset_b": "b",
        "catalog_a_base64": base64.b64encode(catalog() if left is None else left).decode(),
        "catalog_b_base64": base64.b64encode(catalog("two") if right is None else right).decode(),
        **changes,
    }
    return json.dumps(wire, ensure_ascii=False).encode()


def test_service_exact_original_bytes_canonical_page_and_current_evidence():
    left, right = catalog(), catalog("two")
    upload = parse_upload(body(left=left, right=right))
    assert decode_catalog_bytes(upload) == (left, right, None)
    service = ComparisonService()
    encoded = service.create(body(left=left, right=right))
    assert encoded == encode_review_page(make_review_page(create_workspace(left, right)))
    page = decode_review_page(encoded)
    assert len(page.items) == 1 and len(page.items[0].evidence) == 16
    assert all(check.resolution.status == "resolved" for check in page.items[0].evidence)
    assert page.catalogs[0].source_sha256 == hashlib.sha256(left).hexdigest()
    assert service.page(page.workspace_id) == encoded
    assert page.items[0].proposal.proposed_outcome == "review"
    assert service.status()["human_rulings_available"] is False
    assert service.status()["provider_dispatch_available"] is False
    assert left == catalog() and module.make_review_page is make_review_page
    assert module.encode_review_page is encode_review_page


def test_original_utf16_and_explicit_legacy_bytes_keep_source_hashes_and_unicode_fields():
    text = catalog(name="Crème Soap").decode("utf-8")
    left = text.encode("utf-16")
    right = catalog("two", name="Crème Soap").decode("utf-8").encode("cp1252")
    page = decode_review_page(
        ComparisonService().create(body(left=left, right=right, encoding_b="cp1252"))
    )
    assert tuple(c.encoding for c in page.catalogs) == ("utf-16", "cp1252")
    assert tuple(c.source_sha256 for c in page.catalogs) == (
        hashlib.sha256(left).hexdigest(),
        hashlib.sha256(right).hexdigest(),
    )
    assert all(record.raw.name == "Crème Soap" for record in page.items[0].records)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": "1"},
        {"method": "jev"},
        {"method": "llm"},
        {"path": "../CANARY"},
        {"url": "https://example.invalid/CANARY"},
        {"encoding_a": "auto-lossy"},
        {"dataset_a": ""},
        {"dataset_a": "😀" * 70},
    ],
)
def test_closed_upload_shape_method_encoding_and_byte_bounded_namespace_refuse_before_csv(
    changes, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("CSV callback reached")

    monkeypatch.setattr(module, "create_workspace", forbidden)
    service = ComparisonService()
    with pytest.raises(ComparisonInputError) as error:
        service.create(body(**changes))
    assert "CANARY" not in str(error.value) and service.status()["workspace_count"] == 0


@pytest.mark.parametrize("bad", ["data:text/csv;base64,Zm9v", "Zg=", "Zh==", "Zm 9v", "é", "_w=="])
def test_only_exact_canonical_standard_base64_reaches_csv(bad, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("CSV callback reached")

    monkeypatch.setattr(module, "create_workspace", forbidden)
    with pytest.raises(ComparisonInputError):
        ComparisonService().create(body(catalog_a_base64=bad))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b"\xef\xbb\xbf{}",
        b"\xff",
        b'{"nested":' + b"[" * 5 + b"0" + b"]" * 5 + b"}",
        b'{"list":[' + b"0," * 65 + b"0]}",
        b"[]",
        b'{"schema_version":',
    ],
)
def test_json_duplicates_nonfinite_bom_utf8_depth_structure_and_malformed_refuse(raw):
    with pytest.raises(ComparisonInputError):
        parse_upload(raw)


def test_small_alias_byte_bound_before_domain_work(monkeypatch):
    from entity_matcher.api import models

    monkeypatch.setattr(models, "MAX_ALIAS_BYTES", 8)
    with pytest.raises(ComparisonInputError, match="oversized_base64"):
        decode_catalog_bytes(parse_upload(body(aliases_base64=base64.b64encode(b"x" * 9).decode())))


def test_equal_namespaces_invalid_csv_and_failed_serialization_do_not_evict_existing(monkeypatch):
    service = ComparisonService()
    service.create(body())
    before = service.status()
    with pytest.raises(ComparisonInputError, match="namespaces_must_differ"):
        service.create(body(dataset_b="a"))
    with pytest.raises(ComparisonServiceError) as error:
        service.create(body(left=b"CANARY invalid CSV"))
    assert "CANARY" not in str(error.value) and service.status() == before

    def broken(*args, **kwargs):
        raise ValueError("CANARY serialization")

    monkeypatch.setattr(module, "encode_review_page", broken)
    with pytest.raises(ComparisonServiceError) as error:
        service.create(body(dataset_a="new"))
    assert "CANARY" not in str(error.value) and service.status() == before


def test_two_workspace_oldest_eviction_and_identical_upload_does_not_refresh_order():
    service = ComparisonService()
    first = decode_review_page(service.create(body())).workspace_id
    second = decode_review_page(service.create(body(dataset_a="second"))).workspace_id
    assert decode_review_page(service.create(body())).workspace_id == first
    third = decode_review_page(service.create(body(dataset_a="third"))).workspace_id
    assert service.status()["workspace_count"] == 2
    with pytest.raises(ComparisonServiceError, match="not_found"):
        service.page(first)
    assert decode_review_page(service.page(second)).workspace_id == second
    assert decode_review_page(service.page(third)).workspace_id == third


def test_source_byte_retention_limit_evicts_before_count_limit():
    size = len(catalog()) + len(catalog("two"))
    service = ComparisonService(retained_byte_limit=2 * size - 1)
    first = decode_review_page(service.create(body())).workspace_id
    service.create(body(dataset_a="second"))
    assert service.status()["workspace_count"] == 1
    assert service.status()["retained_input_bytes"] == size
    with pytest.raises(ComparisonServiceError, match="not_found"):
        service.page(first)


@pytest.mark.parametrize(
    "raw",
    [
        b"limit=101",
        b"offset=-1",
        b"limit=true",
        b"method=llm",
        b"offset=0&offset=1",
        b"extra=CANARY",
        b"offset=0&limit=1&method=rules&extra=1",
    ],
)
def test_query_bounds_duplicate_and_unknown_fields_refuse(raw):
    with pytest.raises(ComparisonInputError) as error:
        parse_page_query(raw)
    assert "CANARY" not in str(error.value)


def test_default_query_and_existing_pagination_bounds_are_used():
    assert parse_page_query(b"") == {"method": "rules", "offset": 0, "limit": 50}
    service = ComparisonService()
    identity = decode_review_page(service.create(body())).workspace_id
    assert decode_review_page(service.page(identity, offset=1)).items == ()
    for options in ({"offset": 2}, {"limit": 101}, {"offset": True}, {"method": []}):
        with pytest.raises(ComparisonServiceError, match="invalid_page_options"):
            service.page(identity, **options)
    for bad in ("../CANARY", "https://example.invalid", "s4-" + "f" * 64):
        with pytest.raises(ComparisonServiceError, match="not_found"):
            service.page(bad)


def test_generated_closed_upload_schema_matches_actual_model():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/api/comparison-upload.schema.json"
        ).read_bytes()
    )
    assert schema == ComparisonUpload.model_json_schema()
    assert schema["additionalProperties"] is False
