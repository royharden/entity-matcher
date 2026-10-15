"""Tiny authored cross-component controls; no independent DEV/heldout or quality evaluation."""

import csv
import hashlib
import io

import pytest

from entity_matcher.pipeline.workspace import WorkspaceError, create_workspace
from entity_matcher.shared.match.blocking import BlockingPolicy


def catalog(*rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    writer.writerow(("id", "name", "brand", "size", "unit", "price", "category", "description"))
    for record_id, brand in rows:
        writer.writerow((record_id, "Exact Tea", brand, "100", "g", "1.00", "Drinks", ""))
    return stream.getvalue().encode("utf-8")


def test_actual_csv_to_candidates_rules_existing_proposals_and_real_field_registry():
    left, right = catalog(("one", "Acme")), catalog(("two", "Other"))
    before = (left, right)
    workspace = create_workspace(left, right)
    assert workspace.catalogs[0].source_sha256 == hashlib.sha256(left).hexdigest()
    assert workspace.batch.report.retained == 1
    proposals = workspace.inspect(method="rules")
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.proposed_outcome == "review"
    assert proposal.judgment.relatedness == 0.0
    assert "brand_conflict" in proposal.judgment.issue_codes
    assert proposal.judgment.p_same is None and "calibration_unsupported" in proposal.reason_codes
    evidence = workspace.evidence(proposal.pair)
    assert len(evidence) == 14  # seven nonblank raw fields per record, no invented descriptions.
    assert all(item.resolution.status == "resolved" for item in evidence)
    assert any(item.resolution.excerpt == "Exact Tea" for item in evidence)
    assert (left, right) == before


def test_fuzzy_unavailability_is_named_and_cannot_become_terminal(monkeypatch):
    import entity_matcher.shared.match.judges.offline as offline

    monkeypatch.setattr(offline, "_token_ratio", lambda *args: None)
    workspace = create_workspace(catalog(("one", "Acme")), catalog(("two", "Acme")))
    proposal = workspace.inspect(method="fuzzy")[0]
    assert proposal.judgment.status == "unavailable"
    assert proposal.proposed_outcome == "review"
    assert "fuzzy_dependency_unavailable" in proposal.reason_codes
    assert proposal.judgment.relatedness is proposal.judgment.p_same is None


def test_caps_page_count_and_omitted_preview_do_not_hide_retained_denominator():
    workspace = create_workspace(
        catalog(("one", "Acme"), ("three", "Acme")),
        catalog(("two", "Other"), ("four", "Other")),
        policy=BlockingPolicy(per_a_cap=1),
    )
    assert workspace.batch.report.union_enumerated_unique == 4
    assert workspace.batch.report.retained == 2 and workspace.batch.report.per_a_cap_drops == 2
    assert len(workspace.inspect(limit=1)) == 1
    assert len(workspace.inspect(offset=1, limit=1)) == 1
    assert workspace.inspect(offset=2) == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"offset": -1},
        {"offset": True},
        {"offset": 2},
        {"method": "jev"},
        {"method": "llm"},
    ],
)
def test_page_or_uninstalled_method_refuses_without_fallback(kwargs):
    workspace = create_workspace(catalog(("one", "Acme")), catalog(("two", "Other")))
    with pytest.raises(WorkspaceError):
        workspace.inspect(**kwargs)


def test_failed_catalog_or_namespace_collision_cannot_create_partial_workspace():
    with pytest.raises(WorkspaceError, match="catalog_validation_failed"):
        create_workspace(catalog(("one", "Acme")), b"wrong,header\n")
    with pytest.raises(WorkspaceError, match="dataset_namespaces_must_differ"):
        create_workspace(catalog(("one", "Acme")), catalog(("two", "Other")), dataset_b="a")


def test_source_serialization_policy_and_alias_editions_bind_workspace_identity():
    left, right = catalog(("one", "Acme")), catalog(("two", "Other"))
    first = create_workspace(left, right)
    same = create_workspace(left, right)
    assert first.workspace_id == same.workspace_id
    assert (
        create_workspace(left, right, policy=BlockingPolicy(per_a_cap=1)).workspace_id
        != first.workspace_id
    )
    aliases = b"alias,canonical\nAcme,Other\n"
    assert create_workspace(left, right, aliases_csv=aliases).workspace_id != first.workspace_id
    assert create_workspace(left.replace(b"\r\n", b"\n"), right).workspace_id != first.workspace_id


def test_mutated_raw_source_is_not_retargeted_into_proposal_or_evidence():
    workspace = create_workspace(catalog(("one", "Acme")), catalog(("two", "Other")))
    pair = workspace.inspect()[0].pair
    object.__setattr__(workspace.inventory[("a", "one")], "name", "changed")
    proposal = workspace.inspect()[0]
    assert proposal.judgment.status == "invalid"
    assert "stale_workspace_generation" in proposal.reason_codes
    evidence = workspace.evidence(pair)
    assert evidence[0].pointer is None and evidence[0].issue_code == "stale_workspace_generation"


def test_long_ids_remain_visible_with_named_canonical_limit():
    identifier = "x" * 513
    workspace = create_workspace(catalog((identifier, "Acme")), catalog(("two", "Other")))
    proposal = workspace.inspect()[0]
    assert proposal.pair.refs[0].record_id == identifier
    assert "canonical_identity_unsupported" in proposal.reason_codes
    evidence = workspace.evidence(proposal.pair)
    blocked = [item for item in evidence if item.record.record_id == identifier]
    assert len(blocked) == 7 and all(item.pointer is None for item in blocked)
    assert all(item.issue_code == "canonical_identity_unsupported" for item in blocked)


def test_nonascii_namespaces_keep_canonical_pair_orientation_separate_from_input_order():
    workspace = create_workspace(
        catalog(("one", "Acme")), catalog(("two", "Other")), dataset_a="Ω-left", dataset_b="B-right"
    )
    proposal = workspace.inspect()[0]
    assert proposal.pair.refs[0].dataset_id == "B-right"
    assert proposal.judgment.relatedness == 0.0
