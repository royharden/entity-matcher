"""Tiny authored observations exercise fitting; no independent corpus is accessed."""

from dataclasses import replace

import pytest

from entity_matcher.calibrate import CalibrationContext, attach_calibration
from entity_matcher.calibration_fit import (
    CalibrationFitError,
    DevObservation,
    fit_empirical_bins,
)
from entity_matcher.policy import propose_review
from entity_matcher.shared.match.models import JudgmentProposal, PairIdentity, RecordRef


def context(**changes):
    return CalibrationContext(
        **{
            "method": "rules",
            "cohort_id": "authored-toy",
            "dev_manifest_sha256": "a" * 64,
            "config_sha256": "b" * 64,
            **changes,
        }
    )


def observation(i, score=0.9, same=True, **changes):
    return DevObservation(
        str(i),
        context(),
        same,
        JudgmentProposal(method="rules", status="ok", relatedness=score, **changes),
    )


def fit(rows, **changes):
    options = {
        "context": context(),
        "expected_item_ids": tuple(r.item_id for r in rows),
        "edges": (0.0, 0.5, 1.0),
        "min_bin_support": 20,
        **changes,
    }
    return fit_empirical_bins(rows, **options)


def test_exact_empirical_counts_ignore_raw_native_confidence_and_preserve_inputs():
    """Catches a fitter using raw similarity/confidence as probability or altering source labels."""
    rows = tuple(observation(i, same=i < 5, native_confidence=0.99) for i in range(20))
    before = tuple(row.judgment.model_dump_json() for row in rows)
    value = fit(rows)
    assert (value.input_n, value.labelled_n, value.fitted_n) == (20, 20, 20)
    assert value.unlabelled_n == value.unavailable_n == value.issued_n == 0
    assert value.reason_code is None and len(value.model.bins) == 1
    bucket = value.model.bins[0]
    assert (bucket.lo, bucket.hi, bucket.count, bucket.positive_count) == (0.5, 1.0, 20, 5)
    attached = attach_calibration(rows[0].judgment, value.model, expected_context=context())
    assert attached.p_same == 0.25 and attached.relatedness == 0.9
    assert attached.native_confidence == 0.99 and attached.calibration.support_count == 20
    assert tuple(row.judgment.model_dump_json() for row in rows) == before


def test_zero_boundary_internal_boundary_and_one_match_actual_attachment_intervals():
    rows = (observation(0, 0.0, False), observation(1, 0.5, True), observation(2, 1.0, True))
    value = fit(rows, min_bin_support=1)
    assert [(b.count, b.positive_count) for b in value.model.bins] == [(1, 0), (2, 2)]
    for row, expected in zip(rows, (0.0, 1.0, 1.0), strict=True):
        assert (
            attach_calibration(row.judgment, value.model, expected_context=context()).p_same
            == expected
        )


def test_sparse_bins_and_empty_gaps_remain_unsupported_without_invented_counts():
    value = fit(
        (observation(0, 0.1), observation(1, 0.9)), edges=(0.0, 0.2, 0.8, 1.0), min_bin_support=2
    )
    assert [(b.lo, b.hi, b.count) for b in value.model.bins] == [(0.0, 0.2, 1), (0.8, 1.0, 1)]
    for score in (0.1, 0.5, 0.9):
        raw = JudgmentProposal(method="rules", status="ok", relatedness=score)
        assert attach_calibration(raw, value.model, expected_context=context()).p_same is None


def test_exclusions_are_counted_and_never_treated_as_negative_scored_examples():
    rows = (
        observation(0),
        observation(1, same=None),
        replace(
            observation(2),
            judgment=JudgmentProposal(
                method="rules", status="unavailable", issue_codes=("scorer_unavailable",)
            ),
        ),
        observation(3, issue_codes=("unknown_quantity",)),
    )
    value = fit(rows, min_bin_support=1)
    assert (
        value.input_n,
        value.labelled_n,
        value.fitted_n,
        value.unlabelled_n,
        value.unavailable_n,
        value.issued_n,
    ) == (4, 3, 1, 1, 1, 1)
    assert value.model.bins[0].count == value.model.bins[0].positive_count == 1


def test_no_scored_or_empty_population_returns_named_unavailable_not_fake_artifact():
    for rows in (
        (),
        (observation(0, same=None),),
        (observation(0, issue_codes=("unknown_brand",)),),
    ):
        value = fit(rows)
        assert value.model is None and value.reason_code == "no_eligible_scored_observations"
        assert value.fitted_n == 0


@pytest.mark.parametrize("mode", ["missing", "reordered", "duplicate"])
def test_fixed_dev_cohort_cannot_drop_reorder_or_repeat_observations(mode):
    rows = (observation(0), observation(1))
    expected = ("0", "1")
    if mode == "missing":
        rows = rows[:1]
    elif mode == "reordered":
        rows = rows[::-1]
    else:
        rows, expected = (rows[0], rows[0]), ("0", "0")
    with pytest.raises(CalibrationFitError):
        fit(rows, expected_item_ids=expected)


@pytest.mark.parametrize(
    "change",
    [
        {"method": "fuzzy"},
        {"cohort_id": "other"},
        {"dev_manifest_sha256": "c" * 64},
        {"config_sha256": "d" * 64},
    ],
)
def test_observation_cannot_cross_method_cohort_manifest_or_config(change):
    rows = (replace(observation(0), context=context(**change)),)
    with pytest.raises(CalibrationFitError, match="mixed_dev_context"):
        fit(rows)


def test_heldout_context_even_via_model_copy_refuses():
    heldout = context().model_copy(update={"split": "heldout"})
    with pytest.raises(CalibrationFitError, match="invalid_dev_context"):
        fit((observation(0),), context=heldout)
    with pytest.raises(CalibrationFitError, match="invalid_dev_context"):
        fit((replace(observation(0), context=heldout),))


@pytest.mark.parametrize("label", [0, 1, "true", "false"])
def test_only_actual_binary_or_unlabelled_references_are_accepted(label):
    with pytest.raises(CalibrationFitError, match="binary_or_unlabelled"):
        fit((replace(observation(0), same=label),))


def test_wrong_judge_method_and_precalibrated_observation_are_rejected():
    raw = observation(0)
    with pytest.raises(CalibrationFitError, match="mixed_judge_method"):
        fit(
            (replace(raw, judgment=JudgmentProposal(method="fuzzy", status="ok", relatedness=0.9)),)
        )
    value = fit((raw,), min_bin_support=1)
    attached = attach_calibration(raw.judgment, value.model, expected_context=context())
    with pytest.raises(CalibrationFitError, match="precalibrated_input"):
        fit((replace(raw, judgment=attached),))


@pytest.mark.parametrize(
    "edges",
    [
        [0.0, 1.0],
        (0, 1),
        (False, 1.0),
        (0.1, 1.0),
        (0.0, 0.9),
        (0.0, 0.5, 0.5, 1.0),
        (0.0, 0.8, 0.2, 1.0),
        (0.0, float("nan"), 1.0),
    ],
)
def test_explicit_full_finite_strictly_ordered_float_edges_required(edges):
    with pytest.raises(CalibrationFitError, match="fixed_bin_edges"):
        fit((observation(0),), edges=edges)


@pytest.mark.parametrize("support", [True, 0, -1, 1.0, 100_001])
def test_support_floor_is_explicit_bounded_integer(support):
    with pytest.raises(CalibrationFitError, match="minimum_support"):
        fit((observation(0),), min_bin_support=support)


def test_actual_existing_policy_consumes_fitted_artifact_without_wire_change():
    rows = tuple(observation(i) for i in range(20))
    value = fit(rows)
    pair = PairIdentity.from_refs(
        RecordRef(dataset_id="a", record_id="x", content_hash="c" * 64),
        RecordRef(dataset_id="b", record_id="y", content_hash="d" * 64),
    )
    result = propose_review(pair, rows[0].judgment, value.model, expected_context=context())
    assert result.proposed_outcome == "merge" and result.judgment.p_same == 1
    assert result.judgment.calibration.support_count == 20
    # This is an authored mechanism control, not evidence that real quality meets a floor.


def test_fit_needs_no_files_or_network_and_does_not_mutate_explicit_inputs(monkeypatch):
    def denied(*args, **kwargs):
        raise RuntimeError("IO disabled")

    rows = (observation(0),)
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", denied)
        patch.setattr("socket.create_connection", denied)
        with pytest.raises(RuntimeError):
            open("synthetic-canary")
        value = fit(rows, min_bin_support=1)
    assert value.fitted_n == 1 and rows[0].same is True
