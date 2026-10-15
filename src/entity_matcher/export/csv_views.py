"""Literal-text CSV views; no file writes, catalog consolidation or authoritative event codec."""

import csv
from dataclasses import dataclass

from ..domain.components import ProductProjection
from ..shared.match.identity import canonical_bytes
from ..shared.match.models import RecordIdentity

MAX_EXPORT_BYTES = 20 * 1024 * 1024


class CsvExportError(ValueError):
    pass


@dataclass(frozen=True)
class CsvView:
    data: bytes
    rows: int
    prefixed_text_cells: int
    text_policy: str = "apostrophe_prefix_all_body_cells"
    authoritative: bool = False


class _BoundedWriter:
    def __init__(self, maximum):
        if type(maximum) is not int or not 1 <= maximum <= MAX_EXPORT_BYTES:
            raise CsvExportError("invalid_export_byte_bound")
        self.maximum, self.data = maximum, bytearray()

    def write(self, text):
        data = text.encode("utf-8")
        if len(self.data) + len(data) > self.maximum:
            raise CsvExportError("csv_export_byte_limit")
        self.data.extend(data)
        return len(text)


def _view(header, rows, max_bytes):
    buffer = _BoundedWriter(max_bytes)
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(header)
    count = cells = 0
    for row in rows:
        if count >= 20000 or len(row) != len(header) or any(type(v) is not str for v in row):
            raise CsvExportError("invalid_export_row")
        # Every body cell is literal-prefixed, including already-prefixed/empty/control text.
        # These altered display cells must never be used as canonical identity or event inputs.
        writer.writerow(["'" + value for value in row])
        count += 1
        cells += len(row)
    return CsvView(bytes(buffer.data), count, cells)


def component_csv(projection: ProductProjection, *, max_bytes: int = MAX_EXPORT_BYTES) -> CsvView:
    if type(projection) is not ProductProjection:
        raise CsvExportError("product_projection_required")
    unsupported = {
        (item.identity.dataset_id, item.identity.record_id): item.capabilities
        for item in projection.unsupported_records
    }

    def rows():
        seen = set()
        for number, component in enumerate(projection.components, 1):
            if not component.members:
                raise CsvExportError("empty_component")
            status = (
                "contested"
                if component.contested
                else ("human_component" if len(component.members) > 1 else "single_record")
            )
            for member in sorted(
                component.members, key=lambda m: canonical_bytes(m.identity_fields())
            ):
                member = RecordIdentity.model_validate(member)
                identity = (member.dataset_id, member.record_id)
                if identity in seen:
                    raise CsvExportError("duplicate_component_record")
                seen.add(identity)
                blocked = unsupported.get(identity, ())
                yield (
                    str(number),
                    status,
                    member.dataset_id,
                    member.record_id,
                    "unsupported" if "ruling" in blocked else "shape_supported",
                    "unsupported" if "pointer" in blocked else "shape_supported",
                )

    return _view(
        (
            "component_number",
            "component_state",
            "dataset_id",
            "record_id",
            "ruling_identity_shape",
            "pointer_identity_shape",
        ),
        rows(),
        max_bytes,
    )


def pair_csv(projection: ProductProjection, *, max_bytes: int = MAX_EXPORT_BYTES) -> CsvView:
    if type(projection) is not ProductProjection:
        raise CsvExportError("product_projection_required")
    rows = (
        (
            pair.pair_key,
            pair.head_event_id,
            pair.effective_event_id or "",
            pair.state,
            pair.outcome or "",
            "true" if pair.human_evidence_verified else "false",
            pair.queue_state,
            ";".join(pair.issue_codes),
        )
        for pair in projection.pairs
    )
    return _view(
        (
            "pair_key",
            "head_event_id",
            "effective_event_id",
            "state",
            "outcome",
            "human_evidence_checked",
            "candidate_queue",
            "issues",
        ),
        rows,
        max_bytes,
    )
