"""Offline signal controls do not imply accuracy or measured live inference."""

import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from entity_matcher.shared.match.judges.offline import judge_fuzzy, judge_rules
from entity_matcher.shared.match.models import RawProduct
from entity_matcher.shared.match.normalize import normalize_record


def record(
    namespace: str,
    *,
    name: str = "Bottle",
    brand: str = "acme",
    size: str = "500",
    unit: str = "ml",
):
    return normalize_record(
        RawProduct(
            id="1",
            name=name,
            brand=brand,
            size=size,
            unit=unit,
            price="",
            category="",
            description="",
        ),
        namespace,
    )


@pytest.mark.parametrize("judge", [judge_fuzzy, judge_rules])
def test_missing_dependency_named_unavailable_not_substituted(judge) -> None:
    """Catches absent RapidFuzz silently replaced by a different scorer or fake result."""
    with patch.dict(sys.modules, {"rapidfuzz": None, "rapidfuzz.fuzz": None}):
        result = judge(record("a"), record("b"))
    assert result.status == "unavailable" and result.issue_codes == (
        "fuzzy_dependency_unavailable",
    )
    assert result.relatedness is None and result.p_same is None


@pytest.mark.parametrize(
    "right",
    [
        {"size": "1", "unit": "L"},
        {"size": "6 x 500 ml", "unit": ""},
        {"size": "500", "unit": "g"},
        {"brand": "other"},
    ],
)
def test_rules_hard_conflict_is_zero_raw_signal_even_without_fuzzy_dependency(right: dict) -> None:
    """Catches equivalent total volume/brand/dimension conflicts hidden by name similarity."""
    with patch.dict(sys.modules, {"rapidfuzz": None, "rapidfuzz.fuzz": None}):
        result = judge_rules(record("a"), record("b", **right))
    assert result.status == "ok" and result.relatedness == 0.0
    assert result.issue_codes and result.p_same is None and result.calibration is None


@pytest.mark.parametrize("judge", [judge_fuzzy, judge_rules])
def test_invalid_stale_normalization_and_empty_name_have_no_usable_signal(judge) -> None:
    """Catches scoring a changed raw reference, unsupported profile or empty normalized name."""
    source = record("a")
    for bad in (
        replace(source, profile="unknown"),
        replace(source, name=""),
        replace(source, ref=source.ref.model_copy(update={"content_hash": "0" * 64})),
    ):
        result = judge(bad, record("b"))
        assert result.status == "invalid" and result.relatedness is None and result.issue_codes


def test_rule_conflicts_are_symmetric_machine_proposals() -> None:
    """Catches asymmetric constraints or machine rulings appearing in raw-signal output."""
    a, b = record("a"), record("b", size="6 x 500 ml", unit="")
    assert judge_rules(a, b) == judge_rules(b, a)
    assert not {"action", "verdict", "actor", "event_id"} & set(judge_rules(a, b).model_dump())


def test_moved_judgment_and_evidence_consumer_has_no_app_or_provider_import(tmp_path: Path) -> None:
    """Catches raw judgment/evidence helpers importing app storage or live provider modules."""
    repo = Path(__file__).resolve().parents[2]
    source = repo / "src/entity_matcher/shared/match"
    package = tmp_path / "portable_judge"
    package.mkdir()
    for name in (
        "__init__.py",
        "identity.py",
        "models.py",
        "normalize.py",
        "quantity.py",
        "aliases.py",
        "blocking.py",
        "candidates.py",
        "evidence.py",
    ):
        shutil.copyfile(source / name, package / name)
    (package / "judges").mkdir()
    for name in ("__init__.py", "offline.py"):
        shutil.copyfile(source / "judges" / name, package / "judges" / name)
    code = """
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from portable_judge.models import RawProduct
from portable_judge.normalize import normalize_record
from portable_judge.judges.offline import judge_rules
from portable_judge.evidence import field_evidence,resolve_raw_field
def raw(size):
    return RawProduct(
        id="1",name="Bottle",brand="acme",size=size,unit="ml",price="",category="",description="",
    )
a,b=normalize_record(raw("500"),"a"),normalize_record(raw("1000"),"b")
assert judge_rules(a,b).relatedness==0.0
assert judge_rules(a,b).p_same is None
evidence=field_evidence(a,("name",))[0]
assert resolve_raw_field(evidence,dataset_id="a",raw=a.raw).quote=="Bottle"
forbidden=("entity_matcher","shared.providers","shared.rulings","shared.screen","httpx")
assert not any(name.startswith(forbidden) for name in sys.modules)
print(json.dumps({"detached_judge_evidence":True}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"detached_judge_evidence": True}
