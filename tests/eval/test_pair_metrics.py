"""Authored pair handcounts and adversarial denominators; no real DEV evaluation."""

import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from entity_matcher.shared.evalkit import statistics
from entity_matcher.shared.evalkit.models import Outcome
from entity_matcher.shared.match import metrics
from entity_matcher.shared.match.metrics import (
    OperatingCosts,
    PairMetricsError,
    PairQualityFloors,
    assess_pair_quality,
    evaluate_pair_metrics,
)


def row(i, expected, predicted, *, status="ok", eligibility="eligible", human=False):
    return Outcome(
        item_id=str(i),
        group_id="g" + str(i // 2),
        dataset="authored",
        split="dev",
        method="authored",
        field=None,
        **{"class": None},
        eligibility=eligibility,
        status=status,
        correct=None,
        predicted=predicted,
        expected=expected,
        human_touched_prediction=human,
        evidence_origin="not_run" if status == "not_run" else "synthetic",
        issues=(),
        ledger_refs=(),
    )


def evaluate(rows, *, retained=None, costs=None):
    return evaluate_pair_metrics(
        rows,
        expected_item_ids=tuple(r.item_id for r in rows),
        retained_item_ids=frozenset(r.item_id for r in rows) if retained is None else retained,
        costs=costs,
    )


def floors():
    return PairQualityFloors(Decimal("0.98"), Decimal("0.99"), 1, Decimal("0.5"))


def test_handcounts_distinguish_blocking_deferred_terminal_cost_and_burden_populations():
    """Catches deferred positives missing from recall and blocking losses missing from cost."""
    rows = (
        row(0, True, True),
        row(1, False, True),
        row(2, True, None),
        row(3, True, False),
        row(4, True, None, status="blocked"),
        row(5, False, False),
        row(6, None, True, eligibility="unlabelled"),
    )
    value = evaluate(
        rows,
        retained=frozenset(("0", "1", "2", "3", "5", "6")),
        costs=OperatingCosts(Decimal(5), Decimal(1), Decimal("0.2")),
    )
    assert (value.corpus_n, value.eligible_n, value.candidate_eligible_n) == (7, 6, 5)
    assert (value.merge_n, value.review_n, value.unlinked_n, value.answered_n) == (2, 1, 3, 4)
    assert value.counts.model_dump(exclude={"schema_version"}) == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 3,
        "true_negative": 1,
    }
    assert value.blocking_recall.value == 0.75
    assert value.classification.precision.value == 0.5 and value.classification.recall.value == 0.25
    assert value.terminal_positive_misses == 2
    assert value.assumed_labelled_operating_cost == Decimal("7.2")
    assert value.labelled_cohort_review_rate.value == pytest.approx(1 / 6)
    assert value.labelled_candidate_review_rate.value == 0.2
    low, high = value.precision_wilson95
    assert 0 < low < 0.5 < high < 1
    assert assess_pair_quality(value, floors()) == (
        "blocking_recall_below_floor",
        "point_precision_below_floor",
        "automatic_recall_below_floor",
    )


def test_all_review_is_not_successful_zero_false_merge_quality():
    """Catches a vacuous all-review system passing precision/nonzero/automatic recall gates."""
    value = evaluate(tuple(row(i, i < 3, None) for i in range(4)))
    assert value.counts.true_positive == value.counts.false_positive == 0
    assert value.classification.precision.value is None
    assert value.classification.recall.value == 0 and value.precision_wilson95 is None
    assert set(assess_pair_quality(value, floors())) == {
        "point_precision_undefined",
        "automatic_recall_below_floor",
        "true_merges_below_floor",
    }


def test_point_precision_gate_is_not_wilson_lower_bound_gate():
    """Catches silently replacing the adopted point precision gate with its lower interval bound."""
    value = evaluate(tuple(row(i, True, True) for i in range(40)))
    assert value.classification.precision.value == 1 and value.counts.true_positive == 40
    assert value.precision_wilson95[0] < 0.99
    assert assess_pair_quality(value, floors()) == ()


def test_exact_ratio_gate_does_not_round_a_near_boundary_precision():
    value = evaluate(tuple(row(i, i < 99, True) for i in range(100)))
    assert assess_pair_quality(value, floors()) == ()
    stricter = PairQualityFloors(
        Decimal("0.98"), Decimal("0.990000000000000001"), 1, Decimal("0.5")
    )
    assert assess_pair_quality(value, stricter) == ("point_precision_below_floor",)


@pytest.mark.parametrize(
    "status", ["stale", "provider_error", "invalid", "abstained", "unavailable"]
)
def test_failure_raw_payload_cannot_become_a_successful_merge(status):
    value = evaluate((row(0, True, True, status=status),))
    assert value.merge_n == 0 and value.review_n == 1 and value.counts.false_negative == 1


def test_never_run_preserves_population_without_fake_predictions_or_zero_spend():
    value = evaluate((row(0, True, None, status="not_run"), row(1, False, None, status="not_run")))
    assert value.measurement_status == "not_measured" and value.eligible_n == 2
    assert value.counts is value.classification is value.assumed_labelled_operating_cost is None
    assert value.merge_n is value.review_n is value.unlinked_n is None
    assert assess_pair_quality(value, floors()) == ("method_not_measured",)
    assert evaluate(()).measurement_status == "not_measured"


def test_human_touched_flag_and_forged_correctness_do_not_substitute_effective_output():
    value = evaluate((row(0, True, False, human=True).model_copy(update={"correct": True}),))
    assert value.counts.true_positive == 0 and value.counts.false_negative == 1
    assert value.terminal_positive_misses == 1


@pytest.mark.parametrize("mode", ["missing", "reordered", "duplicate", "foreign-candidate"])
def test_fixed_expected_cohort_cannot_silently_drop_duplicate_or_reorder_hard_cases(mode):
    rows = (row(0, True, True), row(1, True, None))
    expected, retained = ("0", "1"), frozenset(("0", "1"))
    if mode == "missing":
        rows = rows[:1]
    elif mode == "reordered":
        rows = rows[::-1]
    elif mode == "duplicate":
        rows, expected = (rows[0], rows[0]), ("0", "0")
    else:
        retained = frozenset(("foreign",))
    with pytest.raises(PairMetricsError):
        evaluate_pair_metrics(rows, expected_item_ids=expected, retained_item_ids=retained)


@pytest.mark.parametrize(
    "change",
    [
        {"dataset": "other"},
        {"split": "heldout"},
        {"method": "other"},
        {"field": "name"},
        {"class_name": "positive"},
        {"predicted": 1},
        {"expected": 0},
    ],
)
def test_mixed_axes_or_nonbinary_json_types_refuse(change):
    rows = (row(0, True, True), row(1, False, False).model_copy(update=change))
    with pytest.raises(PairMetricsError):
        evaluate(rows)


def test_not_run_mixed_with_answers_and_noncandidate_answer_refuse():
    with pytest.raises(PairMetricsError, match="mixed_not_run"):
        evaluate((row(0, True, True), row(1, True, None, status="not_run")))
    with pytest.raises(PairMetricsError, match="noncandidate_answered"):
        evaluate((row(0, True, True),), retained=frozenset())


@pytest.mark.parametrize("bad", [True, 0.2, Decimal("NaN"), Decimal("-1")])
def test_costs_cannot_use_float_boolean_nonfinite_or_negative(bad):
    with pytest.raises(PairMetricsError):
        OperatingCosts(bad, Decimal(1), Decimal("0.2"))


def test_undefined_positive_support_and_no_candidate_burden_stay_null():
    value = evaluate((row(0, False, None, status="blocked"),), retained=frozenset())
    assert value.blocking_recall.value is None and value.classification.recall.value is None
    assert value.labelled_candidate_review_rate.value is None
    assert "automatic_recall_undefined" in assess_pair_quality(value, floors())
    with pytest.raises(PairMetricsError):
        PairQualityFloors(Decimal(1), Decimal(1), 0, Decimal(1))


def test_actual_canonical_math_objects_are_used():
    assert metrics.confusion_counts is statistics.confusion_counts
    assert metrics.classification_rates is statistics.classification_rates
    assert metrics.rate is statistics.rate and metrics.wilson_interval is statistics.wilson_interval


def test_actual_metrics_detached_from_app_owner_sdk_and_network(tmp_path):
    root = Path(__file__).resolve().parents[2] / "src/entity_matcher/shared"
    target = tmp_path / "portable_bundle"
    for relative in (
        "match/metrics.py",
        "evalkit/__init__.py",
        "evalkit/models.py",
        "evalkit/identity.py",
        "evalkit/statistics.py",
    ):
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, dest)
    script = f"""
import importlib,importlib.abc,socket,sys
sys.path.insert(0,{str(tmp_path)!r})
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name.split('.')[0] in {{'entity_matcher','rfr','llm_router_eval','openai','typesafe'}}:
            raise RuntimeError('owner/provider disabled')
sys.meta_path.insert(0,Guard())
def denied(*a,**k): raise RuntimeError('network disabled')
socket.create_connection=denied;socket.socket.connect=denied
from portable_bundle.match.metrics import evaluate_pair_metrics
from portable_bundle.evalkit.models import Outcome
from portable_bundle.evalkit.statistics import confusion_counts
import portable_bundle.match.metrics as actual
assert actual.confusion_counts is confusion_counts
r=Outcome(item_id='one',group_id='group',dataset='authored',split='dev',method='authored',
 field=None,**{{'class':None}},eligibility='eligible',status='ok',correct=None,
 predicted=True,expected=True,human_touched_prediction=False,evidence_origin='synthetic',
 issues=(),ledger_refs=())
result=evaluate_pair_metrics((r,),expected_item_ids=('one',),retained_item_ids=frozenset(('one',)))
assert result.counts.true_positive==1 and result.classification.precision.value==1
for operation in (lambda:importlib.import_module('entity_matcher'),
                  lambda:socket.create_connection(('example.invalid',443))):
    try: operation()
    except RuntimeError: pass
    else: raise AssertionError('negative canary failed')
assert not any(n.split('.')[0] in {{'entity_matcher','rfr','llm_router_eval'}} for n in sys.modules)
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    run = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
