"""Tiny implementation admission controls; no independent heldout body or real seal authored."""

import hashlib
from dataclasses import replace

import pytest
from test_corpus import fixture
from test_runner import Adapter, H, Runner, lookup

from entity_matcher.shared.evalkit.corpus import FrozenCorpusError
from entity_matcher.shared.evalkit.identity import canonical_bytes
from entity_matcher.shared.evalkit.models import GoldRecord, Manifest
from entity_matcher.shared.evalkit.runner import (
    HeldoutAdmission,
    HeldoutHistory,
    MethodConfig,
    compare,
)


def test_heldout_missing_authority_refuses_before_source_or_method_effects(tmp_path):
    rows, manifest, _ = fixture(tmp_path)
    runner = Runner()

    def no_read(gold):
        raise AssertionError("heldout source accessed")

    with pytest.raises(FrozenCorpusError, match="heldout_admission_required"):
        compare(
            {"baseline": MethodConfig("fixture/1", H, "synthetic", runner)},
            manifest,
            "heldout",
            rows,
            Adapter(),
            None,
            no_read,
        )
    assert runner.inputs == []


def synthetic_admission_control(tmp_path):
    """A tiny synthetic wire/port control, not an independent human held-out seal."""
    rows, manifest, _ = fixture(tmp_path)
    rows = tuple(GoldRecord.model_validate(r.model_dump() | {"split": "heldout"}) for r in rows)
    data = manifest.model_dump(mode="json")
    data["splits"][0]["split"] = "heldout"
    data["group_split_map"] = {group: "heldout" for group in data["group_split_map"]}
    ref = {"status": "available", "ref": "synthetic-port-control/1", "sha256": H}
    data["experiment_refs"] |= {"heldout_seal": ref, "dev_config_lock": ref}
    manifest = Manifest.model_validate_json(canonical_bytes(data))
    admission = HeldoutAdmission(
        hashlib.sha256(canonical_bytes(manifest.model_dump(mode="json"))).hexdigest(),
        H,
        H,
        {"baseline": H},
        "1" * 40,
        manifest.run_history_ref,
        None,
    )
    return rows, manifest, admission


class SyntheticHistorySpy(HeldoutHistory):
    """Only an injected refusal spy; creates no persistent exposure or human provenance."""

    def __init__(self):
        self.admissions = []

    def record_exposure(self, admission):
        if self.admissions:
            raise FrozenCorpusError("synthetic_history_rerun_refused")
        self.admissions.append(admission)


def test_explicit_synthetic_admission_records_before_dispatch_and_history_refuses_rerun(tmp_path):
    rows, manifest, admission = synthetic_admission_control(tmp_path)
    history, runner = SyntheticHistorySpy(), Runner()

    def ordered_lookup(gold):
        assert history.admissions == [admission]
        return lookup(gold)

    result = compare(
        {"baseline": MethodConfig("fixture/1", H, "synthetic", runner)},
        manifest,
        "heldout",
        rows,
        Adapter(),
        None,
        ordered_lookup,
        heldout_admission=admission,
        heldout_history=history,
    )
    assert len(result.methods["baseline"].actual_predictions) == 2
    with pytest.raises(FrozenCorpusError, match="synthetic_history_rerun_refused"):
        compare(
            {"baseline": MethodConfig("fixture/1", H, "synthetic", runner)},
            manifest,
            "heldout",
            rows,
            Adapter(),
            None,
            ordered_lookup,
            heldout_admission=replace(admission, rerun_reason="mere reason is not approval"),
            heldout_history=history,
        )
    assert len(runner.inputs) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"manifest_sha256": "b" * 64},
        {"independent_seal_sha256": "b" * 64},
        {"dev_config_sha256": "b" * 64},
        {"method_config_sha256": {"baseline": "b" * 64}},
        {"history_ref": "different-history"},
    ],
    ids=["manifest", "seal", "dev-config", "method-config", "history-ref"],
)
def test_changed_frozen_admission_refuses_before_history_source_or_dispatch(tmp_path, change):
    rows, manifest, admission = synthetic_admission_control(tmp_path)
    history, runner = SyntheticHistorySpy(), Runner()

    def forbidden(gold):
        raise AssertionError("source called before admission")

    with pytest.raises(FrozenCorpusError, match="heldout_frozen_admission_changed"):
        compare(
            {"baseline": MethodConfig("fixture/1", H, "synthetic", runner)},
            manifest,
            "heldout",
            rows,
            Adapter(),
            None,
            forbidden,
            heldout_admission=replace(admission, **change),
            heldout_history=history,
        )
    assert history.admissions == [] and runner.inputs == []
