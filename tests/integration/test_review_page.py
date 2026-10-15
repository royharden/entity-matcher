"""Strict review-page/source boundaries with authored microcases, never cohort fitting or Gold."""

import csv
import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from entity_matcher.pipeline.review_page import (
    MAX_PAGE_BYTES,
    CatalogStamp,
    PageSourceCheck,
    ProductReviewPage,
    ReviewPageError,
    check_page_sources,
    decode_review_page,
    encode_review_page,
    make_review_page,
)
from entity_matcher.pipeline.workspace import WorkspaceError, create_workspace


def catalog(ids, brand):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    writer.writerow(("id", "name", "brand", "size", "unit", "price", "category", "description"))
    for identifier in ids:
        writer.writerow((identifier, "Café Tea", brand, "100", "g", "1", "Drinks", ""))
    return stream.getvalue().encode()


def workspace(*, identifier="one", count=1):
    return create_workspace(
        catalog([identifier], "Acme"), catalog([f"B-{i}" for i in range(count)], "Other")
    )


def page_dict():
    return make_review_page(workspace()).model_dump(mode="json")


def test_actual_page_roundtrip_complete_raw_proposal_and_evidence_with_no_authority():
    source = workspace()
    page = make_review_page(source)
    encoded = encode_review_page(page)
    restored = decode_review_page(encoded)
    assert restored == page and len(restored.items) == 1
    assert len(restored.items[0].evidence) == 14
    assert all(item.resolution.status == "resolved" for item in restored.items[0].evidence)
    assert restored.items[0].proposal.proposed_outcome == "review"
    assert restored.authority == "machine_proposals_only"
    checked = check_page_sources(restored, source)
    assert checked.current
    assert not any(
        (
            checked.proposals_verified,
            checked.evidence_verified,
            checked.human_authority,
            checked.journal_write_authority,
        )
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(actor_label="claimed-human"),
        lambda p: p.update(event_id="invented"),
        lambda p: p.update(sequence=1),
        lambda p: p.update(authority="human_approved"),
        lambda p: p["items"][0].update(actor_provenance="human"),
        lambda p: p["items"][0]["proposal"].update(event_id="invented"),
        lambda p: p["items"][0]["records"][0]["raw"].update(actor="untrusted"),
    ],
)
def test_imported_page_cannot_carry_human_or_journal_authority(mutate):
    value = page_dict()
    mutate(value)
    with pytest.raises(ReviewPageError, match="invalid_review_page"):
        decode_review_page(json.dumps(value).encode())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["items"][0]["records"][0]["raw"].update(name="different"),
        lambda p: p["items"][0]["pair"].update(pair_generation="pg1:" + "a" * 64),
        lambda p: p["items"][0]["records"].reverse(),
        lambda p: p["items"][0]["evidence"].pop(),
        lambda p: p["items"][0]["evidence"].append(p["items"][0]["evidence"][0]),
        lambda p: p["items"][0]["evidence"][0].update(
            pointer=None, resolution=None, issue_code="canonical_identity_unsupported"
        ),
        lambda p: p["items"][0]["evidence"][0]["resolution"].update(checked_source_sha256="f" * 64),
        lambda p: p["report"].update(possible_pairs=2),
        lambda p: p["catalogs"][0].update(row_count_complete=False),
        lambda p: p.update(next_offset=1),
        lambda p: p.update(preview_truncated=True),
        lambda p: p.update(unsupported_records_on_page=1),
        lambda p: p.update(limit=True),
        lambda p: p.update(offset=2),
    ],
)
def test_malformed_raw_bindings_evidence_counts_or_paging_are_rejected(mutate):
    value = page_dict()
    mutate(value)
    with pytest.raises(ReviewPageError):
        decode_review_page(json.dumps(value).encode())


def test_page_count_and_next_offset_keep_all_retained_pairs_visible():
    source = workspace(count=3)
    first = make_review_page(source, limit=2)
    assert len(first.items) == 2 and first.next_offset == 2 and first.preview_truncated
    second = make_review_page(source, offset=2, limit=2)
    assert len(second.items) == 1 and second.next_offset is None and second.report.retained == 3
    assert check_page_sources(second, source).current


def test_unsupported_raw_identity_is_not_dropped_or_replaced():
    identifier = "x" * 513
    page = make_review_page(workspace(identifier=identifier))
    assert page.unsupported_records_on_page == 1
    assert page.items[0].records[0].raw.id == identifier
    assert (
        len(
            [
                check
                for check in page.items[0].evidence
                if check.issue_code == "canonical_identity_unsupported"
            ]
        )
        == 7
    )
    assert decode_review_page(encode_review_page(page)) == page


def test_stamps_detect_current_source_or_processing_change_without_authorizing_actions():
    original = workspace()
    page = make_review_page(original)
    changed = create_workspace(catalog(["one"], "Changed"), catalog(["B-0"], "Other"))
    check = check_page_sources(page, changed)
    assert not check.current and check.issue_code == "workspace_source_changed"
    assert not check.journal_write_authority
    object.__setattr__(original.inventory[("a", "one")], "name", "tampered")
    assert check_page_sources(page, original).issue_code == "raw_record_changed"
    with pytest.raises((WorkspaceError, ReviewPageError)):
        make_review_page(original)


def test_forged_probability_metadata_never_becomes_verified_proposal_or_ruling():
    source = workspace()
    value = make_review_page(source).model_dump(mode="json")
    judgment = value["items"][0]["proposal"]["judgment"]
    judgment["p_same"] = 0.999
    judgment["calibration"] = {
        "dev_manifest_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "support_count": 20,
    }
    value["items"][0]["proposal"]["proposed_outcome"] = "merge"
    imported = decode_review_page(json.dumps(value).encode())
    checked = check_page_sources(imported, source)
    assert (
        checked.current
    )  # This check concerns current source bytes and candidate membership only.
    assert not checked.proposals_verified and not checked.human_authority
    assert not checked.journal_write_authority and imported.authority == "machine_proposals_only"


def test_source_check_constructor_cannot_enable_authority_flags():
    with pytest.raises(TypeError):
        PageSourceCheck(True, None, journal_write_authority=True)


def test_utf8_full_json_exact_byte_boundary_and_no_partial_export():
    page = make_review_page(workspace())
    encoded = encode_review_page(page)
    assert b"Caf\xc3\xa9" in encoded
    assert encode_review_page(page, max_bytes=len(encoded)) == encoded
    with pytest.raises(ReviewPageError, match="review_page_byte_limit"):
        encode_review_page(page, max_bytes=len(encoded) - 1)
    with pytest.raises(ReviewPageError, match="review_page_input_bound"):
        decode_review_page(b" " * (MAX_PAGE_BYTES + 1))
    with pytest.raises(ReviewPageError):
        decode_review_page(b"\xff")


@pytest.mark.parametrize("limit", [0, 101, True])
def test_page_limit_rejected_before_proposal_generation(limit):
    with pytest.raises(WorkspaceError):
        make_review_page(workspace(), limit=limit)


def test_generated_schema_matches_actual_closed_models():
    path = Path(__file__).resolve().parents[2] / "schemas/review/product-review-page.schema.json"
    schema = json.loads(path.read_bytes())
    assert schema == ProductReviewPage.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["items"]["maxItems"] == 100
    with pytest.raises(ValidationError):
        ProductReviewPage.model_validate({})


def test_stamp_dataset_bound_counts_utf8_bytes_and_workspace_id_has_exact_length():
    with pytest.raises(ValidationError):
        CatalogStamp(
            dataset_id="😀" * 100, source_sha256="a" * 64, encoding="utf-8", record_count=0
        )
    value = page_dict()
    value["workspace_id"] += "\n"
    with pytest.raises(ReviewPageError):
        decode_review_page(json.dumps(value).encode())
