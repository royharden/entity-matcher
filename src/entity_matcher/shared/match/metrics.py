"""Internal binary-pair metrics over fixed canonical outcomes; no scoring or data I/O.

Expected IDs and retained IDs must come from the caller's actually frozen corpus
and candidate pass. This adapter checks their shape/order, not their provenance.
Its burden/cost figures describe the labelled cohort, never the whole queue.
"""

from dataclasses import dataclass
from decimal import Decimal

from ..evalkit.identity import ordered_ids_digest
from ..evalkit.models import ConfusionCounts, Outcome
from ..evalkit.statistics import (
    ClassificationStats,
    Statistic,
    classification_rates,
    confusion_counts,
    rate,
    wilson_interval,
)

MAX_CASES = 100_000


class PairMetricsError(ValueError):
    """Code-only invalid population finding; raw values never enter the message."""


def _decimal(value: object, *, probability: bool = False) -> None:
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or value < 0
        or (probability and value > 1)
    ):
        raise PairMetricsError("invalid_explicit_metric_policy")


@dataclass(frozen=True)
class OperatingCosts:
    false_merge: Decimal
    terminal_miss: Decimal
    review: Decimal

    def __post_init__(self):
        for value in (self.false_merge, self.terminal_miss, self.review):
            _decimal(value)


@dataclass(frozen=True)
class PairQualityFloors:
    blocking_recall: Decimal
    point_precision: Decimal
    true_merges: int
    automatic_recall: Decimal

    def __post_init__(self):
        for value in (self.blocking_recall, self.point_precision, self.automatic_recall):
            _decimal(value, probability=True)
        if type(self.true_merges) is not int or self.true_merges < 1:
            raise PairMetricsError("nonzero_true_merge_floor_required")


@dataclass(frozen=True)
class PairMetrics:
    measurement_status: str
    corpus_n: int
    eligible_n: int
    candidate_eligible_n: int
    answered_n: int
    merge_n: int | None
    review_n: int | None
    unlinked_n: int | None
    terminal_positive_misses: int | None
    counts: ConfusionCounts | None
    classification: ClassificationStats | None
    precision_wilson95: tuple[float, float] | None
    blocking_recall: Statistic
    labelled_cohort_review_rate: Statistic | None
    labelled_candidate_review_rate: Statistic | None
    assumed_labelled_operating_cost: Decimal | None


def _population(outcomes, expected_item_ids, retained_item_ids):
    if (
        type(outcomes) is not tuple
        or len(outcomes) > MAX_CASES
        or type(expected_item_ids) is not tuple
        or type(retained_item_ids) is not frozenset
    ):
        raise PairMetricsError("bounded_fixed_population_required")
    try:
        ordered_ids_digest(expected_item_ids)
        if tuple(row.item_id for row in outcomes) != expected_item_ids:
            raise PairMetricsError("fixed_population_order_or_members_changed")
        # Canonical models intentionally do not revalidate every model_copy.
        # Reparse each wire before relying on its status/label/axis constraints.
        rows = tuple(Outcome.model_validate_json(row.model_dump_json()) for row in outcomes)
    except PairMetricsError:
        raise
    except (ValueError, TypeError, AttributeError) as error:
        raise PairMetricsError("invalid_canonical_outcome_population") from error
    if any(type(item) is not str for item in retained_item_ids) or not retained_item_ids.issubset(
        expected_item_ids
    ):
        raise PairMetricsError("foreign_candidate_case")
    if len({(r.dataset, r.split, r.method) for r in rows}) > 1 or any(
        r.field is not None or r.class_name is not None for r in rows
    ):
        raise PairMetricsError("mixed_pair_metric_axes")
    if any(r.status == "not_run" for r in rows) and not all(r.status == "not_run" for r in rows):
        raise PairMetricsError("mixed_not_run_population")
    for row in rows:
        if row.eligibility != "eligible":
            continue
        if type(row.expected) is not bool or (
            row.predicted is not None and type(row.predicted) is not bool
        ):
            raise PairMetricsError("binary_pair_label_or_prediction_required")
        if row.item_id not in retained_item_ids and row.status == "ok":
            raise PairMetricsError("noncandidate_answered")
    return rows


def evaluate_pair_metrics(
    outcomes: tuple[Outcome, ...],
    *,
    expected_item_ids: tuple[str, ...],
    retained_item_ids: frozenset[str],
    costs: OperatingCosts | None = None,
) -> PairMetrics:
    """True/False/None raw predictions mean merge/unlinked/deferred respectively.

    Errors/stale/abstention inside candidates are review. Omitted positives are
    terminal blocking misses. Both are FN for automatic recall. Canonical TN
    means no positive proposal on a negative; it does not mean terminal success.
    No effective human correction substitutes for the supplied raw prediction.
    """
    if costs is not None and type(costs) is not OperatingCosts:
        raise PairMetricsError("explicit_operating_costs_required")
    rows = _population(outcomes, expected_item_ids, retained_item_ids)
    eligible = tuple(row for row in rows if row.eligibility == "eligible")
    candidate_n = sum(row.item_id in retained_item_ids for row in eligible)
    positives = tuple(row for row in eligible if row.expected is True)
    blocking = rate(sum(row.item_id in retained_item_ids for row in positives), len(positives))
    if not rows or all(row.status == "not_run" for row in rows):
        return PairMetrics(
            "not_measured",
            len(rows),
            len(eligible),
            candidate_n,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            blocking,
            None,
            None,
            None,
        )
    merges = sum(row.status == "ok" and row.predicted is True for row in eligible)
    review = sum(
        row.item_id in retained_item_ids and (row.status != "ok" or row.predicted is None)
        for row in eligible
    )
    unlinked = len(eligible) - merges - review
    terminal_misses = sum(
        row.expected is True
        and (
            row.item_id not in retained_item_ids or (row.status == "ok" and row.predicted is False)
        )
        for row in eligible
    )
    counts = confusion_counts(rows, True)
    stats = classification_rates(counts)
    assumed_cost = (
        costs.false_merge * counts.false_positive
        + costs.terminal_miss * terminal_misses
        + costs.review * review
        if costs is not None
        else None
    )
    return PairMetrics(
        "measured",
        len(rows),
        len(eligible),
        candidate_n,
        sum(row.status == "ok" and row.predicted is not None for row in eligible),
        merges,
        review,
        unlinked,
        terminal_misses,
        counts,
        stats,
        wilson_interval(counts.true_positive, counts.true_positive + counts.false_positive),
        blocking,
        rate(review, len(eligible)),
        rate(review, candidate_n),
        assumed_cost,
    )


def assess_pair_quality(metrics: PairMetrics, floors: PairQualityFloors) -> tuple[str, ...]:
    """Apply explicit point gates; the Wilson interval is disclosure, not the gate."""
    if type(metrics) is not PairMetrics or type(floors) is not PairQualityFloors:
        raise PairMetricsError("explicit_quality_inputs_required")
    if metrics.measurement_status != "measured":
        return ("method_not_measured",)
    assert metrics.counts is not None and metrics.classification is not None
    failures = []
    for name, statistic, floor in (
        ("blocking_recall", metrics.blocking_recall, floors.blocking_recall),
        ("point_precision", metrics.classification.precision, floors.point_precision),
        ("automatic_recall", metrics.classification.recall, floors.automatic_recall),
    ):
        # Compare exact integer ratios, avoiding float rounding at the gate.
        if statistic.denominator in (None, 0):
            failures.append(name + "_undefined")
        elif Decimal(statistic.numerator) < floor * statistic.denominator:
            failures.append(name + "_below_floor")
    if metrics.counts.true_positive < floors.true_merges:
        failures.append("true_merges_below_floor")
    return tuple(failures)
