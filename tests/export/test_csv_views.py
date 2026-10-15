"""CSV structural/text controls, not a real spreadsheet execution or canonical roundtrip."""

import csv
import io

import pytest

from entity_matcher.domain.components import (
    Component,
    PairView,
    ProductProjection,
    UnsupportedRecord,
)
from entity_matcher.export.csv_views import CsvExportError, component_csv, pair_csv
from entity_matcher.shared.match.models import RecordIdentity


def projection(record_id):
    identity = RecordIdentity(dataset_id="catalog", record_id=record_id)
    return ProductProjection((Component((identity,), False, ()),), (), (), "clean")


def decoded(view):
    return list(csv.reader(io.StringIO(view.data.decode("utf-8"), newline=""), strict=True))


@pytest.mark.parametrize(
    "record_id",
    [
        "=SUM(1,2)",
        "+1",
        "-1",
        "@SUM(1)",
        "\t=1",
        "\r=1",
        "\n=1",
        "＝1",
        "＋1",
        "－1",
        "＠1",
        'quoted",=1',
        "semi;=1",
        "é,\r\n  Unicode",
        "'already-prefixed",
        "plain",
    ],
)
def test_every_untrusted_body_cell_is_prefixed_and_kept_in_one_csv_record(record_id):
    source = projection(record_id)
    result = component_csv(source)
    rows = decoded(result)
    assert len(rows) == 2 and len(rows[0]) == len(rows[1]) == 6
    assert rows[1][3] == "'" + record_id
    assert all(value.startswith("'") for value in rows[1])
    assert result.rows == 1 and result.prefixed_text_cells == 6
    assert result.authoritative is False
    assert source.components[0].members[0].record_id == record_id


def test_contested_and_unsupported_records_remain_in_counts_and_output():
    a = RecordIdentity(dataset_id="A", record_id="one")
    b = RecordIdentity(dataset_id="B", record_id="two")
    source = ProductProjection(
        (Component((a, b), True, ("negative-pair",)),),
        (),
        (UnsupportedRecord(a, ("pointer",)),),
        "clean",
    )
    result = component_csv(source)
    rows = decoded(result)
    assert result.rows == 2
    assert rows[1][1] == rows[2][1] == "'contested"
    assert rows[1][5] == "'unsupported"


def test_pair_review_barrier_empty_value_outside_queue_and_issues_preserved():
    pair = PairView(
        "pk1:" + "a" * 64,
        "=untrusted",
        None,
        "needs_review",
        None,
        False,
        "outside",
        ("human_evidence_unverified",),
    )
    source = ProductProjection((), (pair,), (), "clean")
    result = pair_csv(source)
    rows = decoded(result)
    assert result.rows == 1 and result.prefixed_text_cells == 8
    assert rows[1][1] == "'=untrusted"
    assert rows[1][2] == rows[1][4] == "'"
    assert rows[1][3] == "'needs_review" and rows[1][6] == "'outside"
    assert rows[1][7] == "'human_evidence_unverified"


def test_whole_export_refused_at_byte_bound_without_external_write():
    with pytest.raises(CsvExportError, match="csv_export_byte_limit"):
        component_csv(projection("x" * 100), max_bytes=200)


@pytest.mark.parametrize("bound", [True, 0, 20 * 1024 * 1024 + 1])
def test_invalid_byte_bound_rejected(bound):
    with pytest.raises(CsvExportError, match="invalid_export_byte_bound"):
        component_csv(projection("one"), max_bytes=bound)


def test_empty_projection_produces_header_only_and_duplicate_members_refuse():
    empty = ProductProjection((), (), (), "clean")
    assert len(decoded(component_csv(empty))) == 1
    assert component_csv(empty).rows == 0
    source = projection("one")
    duplicate = ProductProjection(source.components * 2, (), (), "clean")
    with pytest.raises(CsvExportError, match="duplicate_component_record"):
        component_csv(duplicate)
