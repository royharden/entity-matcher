"""Frozen public source/count controls only; never execute a matcher or infer unknown labels."""

import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from entity_matcher.io.catalog import validate_catalog
from entity_matcher.shared.match.aliases import load_aliases
from entity_matcher.shared.match.identity import RAW_FIELDS

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "fixtures/independent"


@pytest.mark.parametrize(
    "relative,count",
    [
        ("dev/catalog-A.csv", 100),
        ("dev/catalog-B.csv", 100),
        ("demo/catalog-A.csv", 20),
        ("demo/catalog-B.csv", 20),
    ],
)
def test_actual_csv_reader_and_product_parser_preserve_every_raw_string(relative, count):
    data = (DATA / relative).read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""), strict=True)
    assert tuple(reader.fieldnames) == RAW_FIELDS
    decoded = list(reader)
    assert len(decoded) == count
    assert all(
        set(row) == set(RAW_FIELDS) and all(type(v) is str for v in row.values()) for row in decoded
    )
    parsed = validate_catalog(data, "A" if "-A" in relative else "B")
    assert parsed.accepted and not parsed.issues and parsed.row_count_complete
    assert parsed.data_rows_seen == len(parsed.records) == count
    assert [record.model_dump() for record in parsed.records] == decoded
    assert len({record.id for record in parsed.records}) == count
    assert parsed.source_sha256 == hashlib.sha256(data).hexdigest()


def test_all_eight_promoted_source_bytes_match_preuse_approved_pins():
    source = json.loads((DATA / "SOURCE.json").read_bytes())
    assert len(source["members"]) == 8
    for member in source["members"]:
        path = ROOT / member["target"]
        assert path.resolve().is_relative_to(DATA.resolve())
        data = path.read_bytes()
        assert len(data) == member["bytes"]
        assert hashlib.sha256(data).hexdigest() == member["sha256"]
    assert (
        source["approval_sha256"]
        == "df382c6b52b2aebcea965d87f1e16a60ced8d820d8c60b95bbcc6425e4a7a874"
    )


def test_dev_cases_reference_actual_sources_with_exact_provisional_denominator():
    ids = {}
    for namespace in ("A", "B"):
        parsed = validate_catalog((DATA / f"dev/catalog-{namespace}.csv").read_bytes(), namespace)
        assert parsed.accepted
        ids[namespace] = {record.id for record in parsed.records}
    cases = [json.loads(line) for line in (DATA / "dev/cases.jsonl").read_bytes().splitlines()]
    assert len(cases) == 500
    assert len({case["case_id"] for case in cases}) == 500
    assert len({(case["a_id"], case["b_id"]) for case in cases}) == 500
    for case in cases:
        assert set(case) == {"case_id", "a_id", "b_id", "same", "label_source"}
        assert case["a_id"] in ids["A"] and case["b_id"] in ids["B"]
        assert type(case["same"]) is bool
        assert case["label_source"] == "agent_provisional synthetic"
    assert sum(case["same"] for case in cases) == 97
    # These are authored source labels, not measured TP/FP, recall or human agreement.


def test_public_aliases_have_separate_digest_without_overwriting_default_config():
    authored = load_aliases((DATA / "brand-aliases.csv").read_bytes())
    existing = load_aliases((ROOT / "config/brand_aliases.csv").read_bytes())
    assert authored.digest != existing.digest
    provenance = json.loads((DATA / "PROVENANCE.json").read_bytes())
    assert provenance["provider_runs"] == 0
    assert provenance["retail_acquisition"] is False
    assert provenance["label_source"] == "agent_provisional synthetic"


def test_unlabelled_and_ambiguous_omissions_are_not_negatives_or_local_heldout_data():
    source = json.loads((DATA / "SOURCE.json").read_bytes())
    metadata = source["whole_frozen_corpus_metadata_only"]
    assert metadata["ambiguity_omissions"] == 20
    assert metadata["ambiguous_cartesian_pairs"] == 1000
    assert metadata["unlabelled_cartesian_pairs"] == 39000
    assert not (DATA / "heldout").exists()
    assert not (DATA / "private").exists()
    assert all(not entry["path"].startswith("private/") for entry in source["members"])
