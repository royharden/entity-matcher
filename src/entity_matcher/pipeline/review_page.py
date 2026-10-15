"""Bounded nonauthorizing product review pages over actual raw sources and canonical evidence."""

import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ..domain.bindings import CanonicalIdentityUnsupported, product_pointer
from ..shared.cited.models import ProductPointer, Resolution
from ..shared.match.identity import RAW_FIELDS, record_identity
from ..shared.match.models import (
    Code,
    DatasetId,
    Digest,
    FieldId,
    FrozenContract,
    PairIdentity,
    RawProduct,
    RecordRef,
    ReviewProposal,
)
from .workspace import ComparisonWorkspace, WorkspaceError

MAX_PAGE_BYTES = 20 * 1024 * 1024
Count = Annotated[int, Field(strict=True, ge=0, le=100_000_000)]


class ReviewPageError(ValueError):
    pass


class CatalogStamp(FrozenContract):
    dataset_id: DatasetId
    source_sha256: Digest
    encoding: Literal["utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"]
    record_count: Annotated[int, Field(strict=True, ge=0, le=10000)]
    row_count_complete: bool = True

    @model_validator(mode="after")
    def complete_count(self) -> Self:
        record_identity({"dataset_id": self.dataset_id, "record_id": "stamp-validation"})
        if not self.row_count_complete:
            raise ValueError("Review page requires complete source counts")
        return self


class CandidateOverview(FrozenContract):
    profile: Literal["product-candidates-v1"]
    n_a: Annotated[int, Field(strict=True, ge=0, le=10000)]
    n_b: Annotated[int, Field(strict=True, ge=0, le=10000)]
    possible_pairs: Count
    b1_enumerated_unique: Count
    b2_enumerated_unique: Count
    union_enumerated_unique: Count
    uncapped_union_lower_bound: Count
    uncapped_union_upper_bound: Count
    uncapped_union_exact: bool
    retained: Annotated[int, Field(strict=True, ge=0, le=500000)]
    per_a_cap_drops: Count
    global_cap_drops: Count
    per_a_cap: Annotated[int, Field(strict=True, ge=1, le=50)]
    global_cap: Annotated[int, Field(strict=True, ge=1, le=500000)]
    block_omission_count: Count
    token_keys_truncated_records: Annotated[int, Field(strict=True, ge=0, le=20000)]

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if self.possible_pairs != self.n_a * self.n_b:
            raise ValueError("Candidate Cartesian denominator mismatch")
        if (
            self.retained + self.per_a_cap_drops + self.global_cap_drops
            != self.union_enumerated_unique
        ):
            raise ValueError("Candidate cap accounting mismatch")
        if self.retained > min(self.global_cap, self.n_a * self.per_a_cap):
            raise ValueError("Retained count exceeds policy caps")
        if not (
            self.union_enumerated_unique
            == self.uncapped_union_lower_bound
            <= self.uncapped_union_upper_bound
            <= self.possible_pairs
        ):
            raise ValueError("Candidate union bounds mismatch")
        if (
            self.uncapped_union_exact
            and self.uncapped_union_lower_bound != self.uncapped_union_upper_bound
        ):
            raise ValueError("Exact union cannot have an interval")
        if not (
            max(self.b1_enumerated_unique, self.b2_enumerated_unique)
            <= self.union_enumerated_unique
            <= self.b1_enumerated_unique + self.b2_enumerated_unique
        ):
            raise ValueError("Blocker union accounting mismatch")
        return self

    @classmethod
    def from_workspace(cls, workspace: ComparisonWorkspace) -> Self:
        report = workspace.batch.report
        return cls(
            **{
                name: getattr(report, name)
                for name in cls.model_fields
                if name not in {"per_a_cap", "global_cap", "block_omission_count"}
            },
            per_a_cap=report.policy.per_a_cap,
            global_cap=report.policy.global_cap,
            block_omission_count=len(report.block_omissions),
        )


class ReferencedProduct(FrozenContract):
    ref: RecordRef
    raw: RawProduct

    @model_validator(mode="after")
    def exact_raw_reference(self) -> Self:
        if self.ref.record_id != self.raw.id or self.ref.content_hash != self.raw.fingerprint:
            raise ValueError("Raw product disagrees with its reference")
        return self


class FieldCheck(FrozenContract):
    record: RecordRef
    field_id: FieldId
    pointer: ProductPointer | None
    resolution: Resolution | None
    issue_code: Code | None

    @model_validator(mode="after")
    def explicit_evidence_state(self) -> Self:
        if self.pointer is None:
            if self.resolution is not None or self.issue_code != "canonical_identity_unsupported":
                raise ValueError("Missing pointer must be an explicit unsupported identity")
            return self
        pointer = self.pointer
        if (
            (pointer.locator.dataset_id, pointer.locator.record_id, pointer.locator.field_id)
            != (self.record.dataset_id, self.record.record_id, self.field_id)
            or pointer.source_sha256 != self.record.content_hash
            or self.resolution is None
        ):
            raise ValueError("Field evidence reference mismatch")
        resolution = self.resolution
        if resolution.status == "resolved":
            if (
                resolution.scope != "text"
                or self.issue_code is not None
                or (
                    resolution.checked_source_sha256,
                    resolution.checked_source_revision,
                    resolution.checked_artifact_sha256,
                    resolution.excerpt,
                    resolution.excerpt_sha256,
                )
                != (
                    pointer.source_sha256,
                    pointer.source_revision,
                    pointer.evidence_artifact_sha256,
                    pointer.locator.quote,
                    pointer.locator.excerpt_sha256,
                )
            ):
                raise ValueError("Resolved field must retain its exact checked bindings")
        elif self.issue_code != resolution.reason:
            raise ValueError("Unresolved field must preserve its issue code")
        return self


class ReviewItem(FrozenContract):
    pair: PairIdentity
    records: tuple[ReferencedProduct, ReferencedProduct]
    proposal: ReviewProposal
    evidence: Annotated[tuple[FieldCheck, ...], Field(max_length=16)]

    @model_validator(mode="after")
    def complete_aligned_source(self) -> Self:
        if PairIdentity.from_refs(*(item.ref for item in self.records)) != self.pair:
            raise ValueError("Review records disagree with pair identity/generation")
        if tuple(item.ref.stable_identity for item in self.records) != self.pair.refs:
            raise ValueError("Review records require canonical pair orientation")
        if self.proposal.pair != self.pair:
            raise ValueError("Review proposal belongs to another pair")
        expected = {}
        for item in self.records:
            for field in RAW_FIELDS:
                if getattr(item.raw, field):
                    expected[(item.ref.dataset_id, item.ref.record_id, field)] = item
        actual = {}
        for check in self.evidence:
            key = (check.record.dataset_id, check.record.record_id, check.field_id)
            if key not in expected or key in actual or check.record != expected[key].ref:
                raise ValueError("Missing, duplicated or foreign field evidence")
            actual[key] = check
            try:
                pointer = product_pointer(
                    check.record.dataset_id, expected[key].raw, check.field_id
                )
            except CanonicalIdentityUnsupported:
                if check.pointer is not None:
                    raise ValueError("Unsupported identity cannot fabricate a pointer") from None
            else:
                if check.pointer is None or pointer.model_dump(
                    exclude={"provenance_ref"}
                ) != check.pointer.model_dump(exclude={"provenance_ref"}):
                    raise ValueError("Evidence pointer differs from exact original raw field")
        if set(actual) != set(expected):
            raise ValueError("Review item omits nonblank raw field evidence")
        return self


class ProductReviewPage(FrozenContract):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    kind: Literal["product_review_page"] = "product_review_page"
    authority: Literal["machine_proposals_only"] = "machine_proposals_only"
    workspace_id: Annotated[str, Field(pattern=r"^s4-[0-9a-f]{64}$", min_length=67, max_length=67)]
    catalogs: tuple[CatalogStamp, CatalogStamp]
    report: CandidateOverview
    offset: Annotated[int, Field(strict=True, ge=0, le=500000)]
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 50
    items: Annotated[tuple[ReviewItem, ...], Field(max_length=100)]
    next_offset: Annotated[int, Field(strict=True, ge=0, le=500000)] | None
    preview_truncated: bool
    unsupported_records_on_page: Annotated[int, Field(strict=True, ge=0, le=200)]

    @model_validator(mode="after")
    def complete_page_counts(self) -> Self:
        a, b = self.catalogs
        if a.dataset_id == b.dataset_id or (a.record_count, b.record_count) != (
            self.report.n_a,
            self.report.n_b,
        ):
            raise ValueError("Review catalog namespaces/counts disagree")
        if self.offset > self.report.retained or len(self.items) != min(
            self.limit, self.report.retained - self.offset
        ):
            raise ValueError("Review page count/offset mismatch")
        end = self.offset + len(self.items)
        if self.next_offset != (end if end < self.report.retained else None):
            raise ValueError("Review next offset mismatch")
        if self.preview_truncated != (len(self.items) < self.report.retained):
            raise ValueError("Review preview omission mismatch")
        pairs = set()
        unsupported = set()
        for item in self.items:
            if item.pair.pair_key in pairs or {ref.dataset_id for ref in item.pair.refs} != {
                a.dataset_id,
                b.dataset_id,
            }:
                raise ValueError("Duplicate or foreign review pair")
            pairs.add(item.pair.pair_key)
            unsupported.update(
                (check.record.dataset_id, check.record.record_id)
                for check in item.evidence
                if check.issue_code == "canonical_identity_unsupported"
            )
        if len(unsupported) != self.unsupported_records_on_page:
            raise ValueError("Unsupported record count mismatch")
        return self


def make_review_page(
    workspace: ComparisonWorkspace, *, method="rules", offset=0, limit=50
) -> ProductReviewPage:
    proposals = workspace.inspect(method=method, offset=offset, limit=limit)
    items = []
    unsupported = set()
    for proposal in proposals:
        pair = proposal.pair
        refs = tuple(
            RecordRef(**ref.identity_fields(), content_hash=digest)
            for ref, digest in zip(pair.refs, pair.raw_fingerprints, strict=True)
        )
        records = tuple(ReferencedProduct(ref=ref, raw=workspace.record(ref)) for ref in refs)
        checks = workspace.evidence(pair)
        if any(check.field_id is None for check in checks):
            raise ReviewPageError("review_source_generation_changed")
        evidence = tuple(
            FieldCheck(
                record=c.record,
                field_id=c.field_id,
                pointer=c.pointer,
                resolution=c.resolution,
                issue_code=c.issue_code,
            )
            for c in checks
        )
        unsupported.update(
            (c.record.dataset_id, c.record.record_id)
            for c in checks
            if c.issue_code == "canonical_identity_unsupported"
        )
        items.append(ReviewItem(pair=pair, records=records, proposal=proposal, evidence=evidence))
    end = offset + len(items)
    return ProductReviewPage(
        workspace_id=workspace.workspace_id,
        catalogs=tuple(
            CatalogStamp(
                dataset_id=c.dataset_id,
                source_sha256=c.source_sha256,
                encoding=c.encoding,
                record_count=len(c.records),
            )
            for c in workspace.catalogs
        ),
        report=CandidateOverview.from_workspace(workspace),
        offset=offset,
        limit=limit,
        items=tuple(items),
        next_offset=end if end < workspace.batch.report.retained else None,
        preview_truncated=len(items) < workspace.batch.report.retained,
        unsupported_records_on_page=len(unsupported),
    )


def encode_review_page(page: ProductReviewPage, *, max_bytes=MAX_PAGE_BYTES) -> bytes:
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_PAGE_BYTES:
        raise ReviewPageError("invalid_review_page_byte_bound")
    page = ProductReviewPage.model_validate(page)
    output = bytearray()
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    for chunk in encoder.iterencode(page.model_dump(mode="json")):
        encoded = chunk.encode("utf-8")
        if len(output) + len(encoded) > max_bytes:
            raise ReviewPageError("review_page_byte_limit")
        output.extend(encoded)
    return bytes(output)


def decode_review_page(data: bytes) -> ProductReviewPage:
    if type(data) is not bytes or len(data) > MAX_PAGE_BYTES:
        raise ReviewPageError("review_page_input_bound")
    try:
        data.decode("utf-8", errors="strict")
        return ProductReviewPage.model_validate_json(data)
    except (ValueError, UnicodeError):
        raise ReviewPageError("invalid_review_page") from None


@dataclass(frozen=True)
class PageSourceCheck:
    current: bool
    issue_code: str | None
    proposals_verified: bool = dataclass_field(default=False, init=False)
    evidence_verified: bool = dataclass_field(default=False, init=False)
    human_authority: bool = dataclass_field(default=False, init=False)
    journal_write_authority: bool = dataclass_field(default=False, init=False)


def check_page_sources(page: ProductReviewPage, workspace: ComparisonWorkspace) -> PageSourceCheck:
    page = ProductReviewPage.model_validate(page)
    stamps = tuple(
        CatalogStamp(
            dataset_id=c.dataset_id,
            source_sha256=c.source_sha256,
            encoding=c.encoding,
            record_count=len(c.records),
        )
        for c in workspace.catalogs
    )
    if page.workspace_id != workspace.workspace_id or page.catalogs != stamps:
        return PageSourceCheck(False, "workspace_source_changed")
    if page.report != CandidateOverview.from_workspace(workspace):
        return PageSourceCheck(False, "candidate_report_changed")
    expected = workspace.batch.candidates[page.offset : page.offset + page.limit]
    if tuple(item.pair.pair_key for item in page.items) != tuple(
        item.pair_key for item in expected
    ):
        return PageSourceCheck(False, "candidate_page_changed")
    try:
        for item in page.items:
            for record in item.records:
                if workspace.record(record.ref) != record.raw:
                    return PageSourceCheck(False, "raw_record_changed")
    except WorkspaceError:
        return PageSourceCheck(False, "raw_record_changed")
    return PageSourceCheck(True, None)
