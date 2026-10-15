"""Pure empirical-bin producer using the existing DEV calibration wire.

All inputs are supplied by a trusted caller. Context hashes and expected IDs are
checked for consistency; they do not establish corpus authority or authorize a
real fit. This module reads no data, runs no judge, chooses no thresholds, and
does not certify model quality or independent support.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass

from .calibrate import CalibrationContext, CalibrationModel, EmpiricalBin
from .shared.evalkit.identity import ordered_ids_digest
from .shared.match.models import JudgmentProposal

MAX_OBSERVATIONS = 100_000


class CalibrationFitError(ValueError):
    """Code-only refusal; labels, record IDs and raw proposals stay out of errors."""


@dataclass(frozen=True)
class DevObservation:
    item_id: str
    context: CalibrationContext
    same: bool | None
    judgment: JudgmentProposal


@dataclass(frozen=True)
class CalibrationFit:
    model: CalibrationModel | None
    input_n: int
    labelled_n: int
    fitted_n: int
    unlabelled_n: int
    unavailable_n: int
    issued_n: int
    reason_code: str | None


def _context(value):
    if type(value) is not CalibrationContext:
        raise CalibrationFitError("explicit_dev_context_required")
    try:
        return CalibrationContext.model_validate_json(value.model_dump_json())
    except ValueError as error:
        raise CalibrationFitError("invalid_dev_context") from error


def fit_empirical_bins(
    observations: tuple[DevObservation, ...],
    *,
    context: CalibrationContext,
    expected_item_ids: tuple[str, ...],
    edges: tuple[float, ...],
    min_bin_support: int,
) -> CalibrationFit:
    """Fit explicit fixed bins, keeping unavailable/issued/unlabelled exclusions counted.

    Only uncalibrated, issue-free OK judgments with binary labels contribute to
    bins. Intervals match attach_calibration: [lo,hi), except hi=1 includes1.
    Empty bins stay absent, sparse bins stay present but unsupported at attach.
    """
    context = _context(context)
    if (
        type(observations) is not tuple
        or len(observations) > MAX_OBSERVATIONS
        or type(expected_item_ids) is not tuple
        or any(type(row) is not DevObservation for row in observations)
    ):
        raise CalibrationFitError("bounded_fixed_dev_observations_required")
    try:
        ordered_ids_digest(expected_item_ids)
    except (ValueError, TypeError) as error:
        raise CalibrationFitError("invalid_fixed_dev_ids") from error
    if tuple(row.item_id for row in observations) != expected_item_ids or any(
        type(row.item_id) is not str or not 1 <= len(row.item_id) <= 512 for row in observations
    ):
        raise CalibrationFitError("fixed_dev_population_changed")
    if (
        type(edges) is not tuple
        or not 2 <= len(edges) <= 101
        or any(type(edge) is not float or not math.isfinite(edge) for edge in edges)
        or edges[0] != 0.0
        or edges[-1] != 1.0
        or any(lo >= hi for lo, hi in zip(edges, edges[1:], strict=False))
    ):
        raise CalibrationFitError("explicit_fixed_bin_edges_required")
    if type(min_bin_support) is not int or not 1 <= min_bin_support <= MAX_OBSERVATIONS:
        raise CalibrationFitError("explicit_minimum_support_required")
    counts = [[0, 0] for _ in range(len(edges) - 1)]
    labelled = fitted = unknown = unavailable = issued = 0
    for observation in observations:
        if _context(observation.context) != context:
            raise CalibrationFitError("mixed_dev_context")
        if observation.same is not None and type(observation.same) is not bool:
            raise CalibrationFitError("binary_or_unlabelled_reference_required")
        if type(observation.judgment) is not JudgmentProposal:
            raise CalibrationFitError("raw_judgment_required")
        try:
            judgment = JudgmentProposal.model_validate_json(observation.judgment.model_dump_json())
        except ValueError as error:
            raise CalibrationFitError("invalid_raw_judgment") from error
        if judgment.method != context.method:
            raise CalibrationFitError("mixed_judge_method")
        if judgment.p_same is not None or judgment.calibration is not None:
            raise CalibrationFitError("precalibrated_input_refused")
        if observation.same is None:
            unknown += 1
            continue
        labelled += 1
        if judgment.status != "ok":
            unavailable += 1
            continue
        if judgment.issue_codes:
            issued += 1
            continue
        score = judgment.relatedness
        assert score is not None
        index = min(bisect_right(edges, score) - 1, len(counts) - 1)
        counts[index][0] += 1
        counts[index][1] += observation.same
        fitted += 1
    bins = tuple(
        EmpiricalBin(lo=edges[i], hi=edges[i + 1], count=n, positive_count=positive)
        for i, (n, positive) in enumerate(counts)
        if n
    )
    model = (
        CalibrationModel(context=context, min_bin_support=min_bin_support, bins=bins)
        if bins
        else None
    )
    return CalibrationFit(
        model,
        len(observations),
        labelled,
        fitted,
        unknown,
        unavailable,
        issued,
        None if model is not None else "no_eligible_scored_observations",
    )
