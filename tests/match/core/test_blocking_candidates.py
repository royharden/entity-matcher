"""Small hand-oracle retrieval/cap controls; not a 10k x10k stress measurement."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from entity_matcher.shared.match.blocking import BlockingPolicy
from entity_matcher.shared.match.candidates import generate_candidates
from entity_matcher.shared.match.models import RawProduct
from entity_matcher.shared.match.normalize import normalize_record


def record(
    dataset: str,
    id: str,
    name: str,
    *,
    brand: str = "",
    size: str = "",
    unit: str = "",
    price: str = "",
):
    return normalize_record(
        RawProduct(
            id=id,
            name=name,
            brand=brand,
            size=size,
            unit=unit,
            price=price,
            category="",
            description="",
        ),
        dataset,
    )


def pair_ids(batch) -> set[tuple[str, str]]:
    return {(pair.refs[0].record_id, pair.refs[1].record_id) for pair in batch.candidates}


def test_two_blockers_union_matches_independent_five_pair_oracle() -> None:
    """Catches absent B2-only retrieval, token matches, unknown-name keys or double counting."""
    left = (
        record("a", "a1", "red bottle", brand="Acme", size="500", unit="ml"),
        record("a", "a2", "unique alpha", brand="Acme", size="1", unit="L"),
        record("a", "a3", "mystery"),
    )
    right = (
        record("b", "b1", "red bottle", brand="Other", size="500", unit="ml"),
        record("b", "b2", "wholly distinct", brand="Acme", size="0.5", unit="L"),
        record("b", "b3", "alpha variant"),
        record("b", "b4", "other", brand="Acme", size="1020", unit="ml"),
        record("b", "b5", "mystery"),
    )
    batch = generate_candidates(left, right)
    assert pair_ids(batch) == {("a1", "b1"), ("a1", "b2"), ("a2", "b3"), ("a2", "b4"), ("a3", "b5")}
    report = batch.report
    assert (report.possible_pairs, report.b1_enumerated_unique, report.b2_enumerated_unique) == (
        15,
        3,
        2,
    )
    assert report.union_enumerated_unique == report.retained == 5
    assert (
        report.uncapped_union_exact
        and report.uncapped_union_lower_bound == report.uncapped_union_upper_bound == 5
    )
    assert (
        batch.candidates[0].refs[0].record_id == "a1"
        and batch.candidates[0].refs[1].record_id == "b1"
    )
    assert next(p for p in batch.candidates if p.refs[1].record_id == "b2").blockers == ("B2",)


def test_both_blockers_deduplicate_and_pack_dimension_are_distinct() -> None:
    """Catches B1/B2 overlap counted twice and equal total volume overriding exact packs."""
    a = record("a", "1", "red bottle", brand="Acme", size="500", unit="ml")
    b = record("b", "1", "red bottle", brand="Acme", size="0.5", unit="L")
    batch = generate_candidates((a,), (b,))
    assert len(batch.candidates) == 1 and batch.candidates[0].blockers == ("B1", "B2")
    assert (
        batch.report.b1_enumerated_unique,
        batch.report.b2_enumerated_unique,
        batch.report.union_enumerated_unique,
    ) == (1, 1, 1)
    a = record("a", "1", "multi", brand="Acme", size="6 x 500 ml")
    other = record("b", "1", "single", brand="Acme", size="3", unit="L")
    assert not generate_candidates((a,), (other,)).candidates
    unknown = record("b", "2", "different", brand="Acme")
    assert not generate_candidates((a,), (unknown,)).candidates


def test_quantity_neighbors_retrieve_two_percent_boundary() -> None:
    """Catches size buckets losing an equivalent quantity at their boundary."""
    a = record("a", "1", "left", brand="Acme", size="490", unit="ml")
    b = record("b", "1", "right", brand="Acme", size="500", unit="ml")
    assert generate_candidates((a,), (b,)).candidates[0].blockers == ("B2",)


def test_overlap_priority_then_canonical_refs_caps_with_exact_drop_digest() -> None:
    """Catches arrival-order caps and nondisclosed per-A candidate losses."""
    a = record("a", "1", "same", brand="Acme", size="500", unit="ml")
    b1 = record("b", "1", "same")
    b2 = record("b", "2", "same", brand="Acme", size="500", unit="ml")
    b3 = record("b", "3", "same")
    policy = BlockingPolicy(per_a_cap=2)
    batch = generate_candidates((a,), (b3, b1, b2), policy=policy)
    assert [p.refs[1].record_id for p in batch.candidates] == ["2", "1"]
    assert batch.report.per_a_cap_drops == 1 and batch.report.global_cap_drops == 0
    expected = b'[{"dataset_id":"a","record_id":"1"},{"dataset_id":"b","record_id":"3"}]\n'
    assert batch.report.row_caps[0].cap_drop_identity_sha256 == hashlib.sha256(expected).hexdigest()
    assert batch.report == generate_candidates((a,), (b2, b3, b1), policy=policy).report


def test_global_heap_replaces_earlier_lower_overlap_with_provenance() -> None:
    """Catches first-encounter global caps or lost replacement/drop accounting."""
    a1, a2, b = record("a", "1", "red variant"), record("a", "2", "red"), record("b", "1", "red")
    batch = generate_candidates((a2, a1), (b,), policy=BlockingPolicy(global_cap=1))
    assert pair_ids(batch) == {("2", "1")}
    assert (
        batch.report.global_cap_drops == 1
        and batch.report.global_cap_drop_identity_sha256 is not None
    )
    assert batch == generate_candidates((a1, a2), (b,), policy=BlockingPolicy(global_cap=1))


def test_oversized_posting_pruning_refinement_discloses_bounds() -> None:
    """Catches capped/pruned union reported as an exact original candidate population."""
    a = record("a", "1", "common", brand="Acme", size="500", unit="ml")
    right = tuple(
        record("b", str(i), "common", brand="Acme", size="500", unit="ml") for i in range(5)
    )
    batch = generate_candidates(
        (a,), right, policy=BlockingPolicy(posting_cap=2, token_frequency_cap=2)
    )
    report = batch.report
    assert report.retained == report.union_enumerated_unique == 2
    assert not report.uncapped_union_exact
    assert (report.uncapped_union_lower_bound, report.uncapped_union_upper_bound) == (2, 5)
    assert {o.reason for o in report.block_omissions} == {
        "high_frequency_token",
        "posting_refined_and_capped",
    }
    assert all(o.original_posting_count == 5 for o in report.block_omissions)
    assert batch == generate_candidates(
        (a,), tuple(reversed(right)), policy=BlockingPolicy(posting_cap=2, token_frequency_cap=2)
    )


def test_token_key_truncation_is_reported_not_hidden_as_exact_union() -> None:
    """Catches bounded name-key selection silently changing reported original blocker population."""
    a = record("a", "1", " ".join(f"word{i}" for i in range(17)))
    b = record("b", "1", "word16")
    batch = generate_candidates((a,), (b,))
    assert batch.report.token_keys_truncated_records == 1 and not batch.report.uncapped_union_exact


def test_namespaces_and_raw_mutation_preserve_keys_change_generations() -> None:
    """Catches same opaque IDs across catalogs conflated or price changes altering stable key."""
    a, b = record("a", "1", "same", price="1.00"), record("b", "1", "same")
    old = generate_candidates((a,), (b,)).candidates[0]
    new = generate_candidates((record("a", "1", "same", price="1.0"),), (b,)).candidates[0]
    assert old.pair_key == new.pair_key and old.pair_generation != new.pair_generation
    assert old.refs[0].dataset_id != old.refs[1].dataset_id
    with pytest.raises(ValueError):
        generate_candidates((a,), (a,))
    with pytest.raises(ValueError):
        generate_candidates((a, a), (b,))


@pytest.mark.parametrize(
    "options",
    [
        {"per_a_cap": 51},
        {"global_cap": 500001},
        {"posting_cap": 0},
        {"token_frequency_cap": True},
        {"token_keys_cap": 65},
    ],
)
def test_policy_cannot_bypass_declared_hard_bounds(options: dict) -> None:
    """Catches configurable caps allowing unbounded or bool-as-integer execution."""
    with pytest.raises(ValueError):
        BlockingPolicy(**options)


def test_empty_population_has_null_reduction_and_exact_zero() -> None:
    """Catches empty denominator division and invented candidate counts."""
    batch = generate_candidates((), ())
    assert batch.report.possible_pairs == batch.report.retained == 0
    assert batch.report.reduction is None and batch.report.uncapped_union_exact


def test_global_cap_reject_branch_retains_earlier_better_pair_with_exact_digest() -> None:
    """Catches global rejected entries missing from reproducible drop provenance."""
    a1, a2, b = record("a", "1", "red"), record("a", "2", "red variant"), record("b", "1", "red")
    batch = generate_candidates((a1, a2), (b,), policy=BlockingPolicy(global_cap=1))
    assert pair_ids(batch) == {("1", "1")}
    expected = b'[{"dataset_id":"a","record_id":"2"},{"dataset_id":"b","record_id":"1"}]\n'
    assert batch.report.global_cap_drop_identity_sha256 == hashlib.sha256(expected).hexdigest()
    assert batch.report.global_cap_drops == 1


def test_candidate_row_fence_rejects_without_running_large_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches direct core callers bypassing the importer row limit."""
    from entity_matcher.shared.match import candidates

    assert candidates.MAX_CATALOG_ROWS == 10_000
    monkeypatch.setattr(candidates, "MAX_CATALOG_ROWS", 1)
    with pytest.raises(ValueError):
        generate_candidates((record("a", "1", "red"), record("a", "2", "red")), ())


def test_detached_eight_file_core_consumer(tmp_path: Path) -> None:
    """Catches reusable core importing application I/O, providers, rulings, UI or workspace code."""
    package = tmp_path / "portable_core"
    package.mkdir()
    source = Path(__file__).resolve().parents[3] / "src/entity_matcher/shared/match"
    for filename in (
        "__init__.py",
        "identity.py",
        "models.py",
        "normalize.py",
        "quantity.py",
        "aliases.py",
        "blocking.py",
        "candidates.py",
    ):
        shutil.copyfile(source / filename, package / filename)
    code = """
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from portable_core.models import RawProduct
from portable_core.normalize import normalize_record
from portable_core.candidates import generate_candidates
raw=RawProduct(id="1",name="Bottle",brand="",size="",unit="",price="",category="",description="")
batch=generate_candidates((normalize_record(raw,"a"),),(normalize_record(raw,"b"),))
assert batch.report.retained==1
forbidden=("entity_matcher","shared.providers","shared.rulings","PIL")
assert not any(n.startswith(forbidden) for n in sys.modules)
print(json.dumps({"detached_core":True,"candidates":len(batch.candidates)}))
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
    assert json.loads(result.stdout) == {"detached_core": True, "candidates": 1}
