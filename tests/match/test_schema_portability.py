"""Schema drift controls and detached model-only consumer import."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_match_schemas as generator

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_generator(monkeypatch: pytest.MonkeyPatch, out: Path, *, check: bool = False) -> int:
    args = ["generate_match_schemas", "--out", str(out)]
    if check:
        args.append("--check")
    monkeypatch.setattr(sys, "argv", args)
    return generator.main()


def test_committed_schemas_exactly_match_owned_model_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches stale or missing checked-in schemas while CI otherwise accepts model changes."""
    out = REPO_ROOT / "schemas/match"
    assert run_generator(monkeypatch, out, check=True) == 0
    assert len(generator.schemas()) == 8
    for schema in generator.schemas().values():
        assert schema["additionalProperties"] is False
        for nested in schema.get("$defs", {}).values():
            if nested.get("type") == "object":
                assert nested["additionalProperties"] is False


@pytest.mark.parametrize("damage", ["missing", "extra", "changed", "malformed"])
def test_schema_check_rejects_inventory_value_and_json_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    """Positive control catches a schema checker that always reports parity."""
    assert run_generator(monkeypatch, tmp_path) == 0
    assert run_generator(monkeypatch, tmp_path, check=True) == 0
    path = tmp_path / "RawProduct.schema.json"
    if damage == "missing":
        path.unlink()
    elif damage == "extra":
        (tmp_path / "Unexpected.schema.json").write_text("{}", encoding="utf-8")
    elif damage == "changed":
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["additionalProperties"] = True
        path.write_text(json.dumps(schema), encoding="utf-8")
    else:
        path.write_text("{broken", encoding="utf-8")
    assert run_generator(monkeypatch, tmp_path, check=True) == 1


def test_moved_model_only_consumer_has_no_application_or_workspace_import(tmp_path: Path) -> None:
    """Catches hardcoded entity_matcher or foreign owner imports in reusable model source."""
    consumer = tmp_path / "portable_match"
    consumer.mkdir()
    source = REPO_ROOT / "src/entity_matcher/shared/match"
    for filename in ("__init__.py", "identity.py", "models.py"):
        shutil.copyfile(source / filename, consumer / filename)
    program = r"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from portable_match.models import (
    RawProduct, RecordRef, PairIdentity, JudgmentProposal, ReviewProposal,
)

raw = RawProduct(
    id="1", name="Bottle", brand="", size="", unit="", price="1.00", category="", description="",
)
left, right = RecordRef.from_raw("a", raw), RecordRef.from_raw("b", raw)
pair = PairIdentity.from_refs(right, left)
assert PairIdentity.model_validate_json(pair.model_dump_json()) == pair
review = ReviewProposal(
    pair=pair, judgment=JudgmentProposal(method="rules", status="ok", relatedness=0.8),
    proposed_outcome="review",
)
assert ReviewProposal.model_validate_json(review.model_dump_json()) == review
assert ReviewProposal.model_json_schema()["additionalProperties"] is False
forbidden = (
    "entity_matcher", "shared.providers", "shared.rulings", "shared.screen", "pytesseract", "PIL",
)
for module_name in sys.modules:
    assert not module_name.startswith(forbidden), module_name
for name in ("portable_match", "portable_match.identity", "portable_match.models"):
    path = Path(sys.modules[name].__file__).resolve()
    assert path.is_relative_to(Path.cwd().resolve())
print(json.dumps(
    {"detached_model_only": True, "pair_key": pair.pair_key, "schemas": True}, sort_keys=True,
))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["detached_model_only"] is True and observed["schemas"] is True
