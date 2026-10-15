"""Internal bounded CSV/candidate/proposal/evidence workspace, without I/O or external schema."""

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType

from ..domain.bindings import CanonicalIdentityUnsupported, product_pointer, ruling_binding
from ..domain.product_rows import CurrentProduct, ProductRowResolver, install_product_rows
from ..io.catalog import CatalogValidation, validate_catalog
from ..shared.cited import ArtifactRef, Registry, ResolutionContext
from ..shared.cited.models import ProductPointer, Resolution
from ..shared.match.aliases import load_aliases
from ..shared.match.blocking import BlockingPolicy
from ..shared.match.candidates import CandidateBatch, generate_candidates
from ..shared.match.identity import RAW_FIELDS, canonical_bytes
from ..shared.match.judges.offline import judge_fuzzy, judge_rules
from ..shared.match.models import (
    FieldId,
    JudgmentProposal,
    PairIdentity,
    RawProduct,
    RecordRef,
    ReviewProposal,
)
from ..shared.match.normalize import NormalizedRecord, normalize_record


class WorkspaceError(ValueError):
    """Code-only failure; never include source cells, paths or arbitrary exception bodies."""


@dataclass(frozen=True)
class EvidenceCheck:
    record: RecordRef
    field_id: FieldId | None
    pointer: ProductPointer | None
    resolution: Resolution | None
    issue_code: str | None


class _WorkspaceRead:
    def __init__(self, namespace, rows, reference):
        self.namespace, self.rows, self.reference = namespace, rows, reference

    def decision(self, namespace, dataset, record):
        return (
            "allow" if namespace == self.namespace and (dataset, record) in self.rows else "unknown"
        )

    def current(self, namespace, dataset, record):
        if namespace != self.namespace:
            return CurrentProduct("unknown")
        row = self.rows.get((dataset, record))
        return CurrentProduct("current", row) if row is not None else CurrentProduct("tombstoned")


@dataclass(frozen=True)
class ComparisonWorkspace:
    workspace_id: str
    catalogs: tuple[CatalogValidation, CatalogValidation]
    batch: CandidateBatch
    alias_digest: str | None
    _records: Mapping[tuple[str, str], RawProduct]
    _normalized: Mapping[tuple[str, str], NormalizedRecord]
    _registry: Registry
    _context: ResolutionContext

    @property
    def inventory(self) -> Mapping[tuple[str, str], RawProduct]:
        return self._records

    def record(self, reference: RecordRef) -> RawProduct:
        reference = RecordRef.model_validate(reference)
        raw = self._records.get((reference.dataset_id, reference.record_id))
        if raw is None:
            raise WorkspaceError("workspace_record_missing")
        raw = RawProduct.model_validate(raw)
        if raw.id != reference.record_id or raw.fingerprint != reference.content_hash:
            raise WorkspaceError("stale_workspace_generation")
        return raw

    def inspect(
        self, *, method: str = "rules", offset: int = 0, limit: int = 50
    ) -> tuple[ReviewProposal, ...]:
        if method not in ("rules", "fuzzy"):
            raise WorkspaceError("offline_method_unavailable")
        if (
            type(offset) is not int
            or not 0 <= offset <= len(self.batch.candidates)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise WorkspaceError("workspace_page_bound")
        judge = judge_rules if method == "rules" else judge_fuzzy
        output = []
        for candidate in self.batch.candidates[offset : offset + limit]:
            pair = PairIdentity.from_refs(*candidate.refs)
            # Keep the uploaded A/B order for judging; canonical pair orientation remains separate.
            order = {catalog.dataset_id: i for i, catalog in enumerate(self.catalogs)}
            ordered = sorted(candidate.refs, key=lambda ref: order[ref.dataset_id])
            try:
                for ref in ordered:
                    self.record(ref)
                judgment = judge(
                    *(self._normalized[(ref.dataset_id, ref.record_id)] for ref in ordered)
                )
                for ref in ordered:
                    self.record(ref)
            except WorkspaceError:
                judgment = JudgmentProposal(
                    method=method, status="invalid", issue_codes=("stale_workspace_generation",)
                )
            reasons = list(judgment.issue_codes)
            try:
                ruling_binding(pair)
            except CanonicalIdentityUnsupported:
                reasons.append("canonical_identity_unsupported")
            reasons.append("calibration_unsupported")
            output.append(
                ReviewProposal(
                    pair=pair,
                    judgment=judgment,
                    proposed_outcome="review",
                    reason_codes=tuple(dict.fromkeys(reasons))[:32],
                )
            )
        return tuple(output)

    def evidence(self, pair: PairIdentity) -> tuple[EvidenceCheck, ...]:
        pair = PairIdentity.model_validate(pair)
        result = []
        for identity, content_hash in zip(pair.refs, pair.raw_fingerprints, strict=True):
            ref = RecordRef(**identity.identity_fields(), content_hash=content_hash)
            try:
                raw = self.record(ref)
            except WorkspaceError as error:
                result.append(EvidenceCheck(ref, None, None, None, str(error)))
                continue
            for field in RAW_FIELDS:
                if not getattr(raw, field):
                    continue
                try:
                    pointer = product_pointer(ref.dataset_id, raw, field)
                except CanonicalIdentityUnsupported:
                    result.append(
                        EvidenceCheck(ref, field, None, None, "canonical_identity_unsupported")
                    )
                    continue
                resolution = self._registry.resolve(pointer, self._context)
                result.append(
                    EvidenceCheck(
                        ref,
                        field,
                        pointer,
                        resolution,
                        None if resolution.status == "resolved" else resolution.reason,
                    )
                )
        return tuple(result)


def create_workspace(
    left: bytes,
    right: bytes,
    *,
    dataset_a: str = "a",
    dataset_b: str = "b",
    encoding_a: str | None = None,
    encoding_b: str | None = None,
    aliases_csv: bytes | None = None,
    policy: BlockingPolicy | None = None,
) -> ComparisonWorkspace:
    if dataset_a == dataset_b:
        raise WorkspaceError("dataset_namespaces_must_differ")
    a = validate_catalog(left, dataset_a, encoding=encoding_a)
    b = validate_catalog(right, dataset_b, encoding=encoding_b)
    if not a.accepted or not b.accepted:
        raise WorkspaceError("catalog_validation_failed")
    aliases = load_aliases(aliases_csv) if aliases_csv is not None else None
    policy = policy or BlockingPolicy()
    normalized = tuple(
        tuple(normalize_record(raw, catalog.dataset_id, aliases=aliases) for raw in catalog.records)
        for catalog in (a, b)
    )
    batch = generate_candidates(*normalized, policy=policy)
    index = MappingProxyType(
        {
            (record.ref.dataset_id, record.ref.record_id): record
            for records in normalized
            for record in records
        }
    )
    rows = MappingProxyType({key: record.raw for key, record in index.items()})
    alias_digest = aliases.digest if aliases is not None else None
    digest = hashlib.sha256(
        canonical_bytes(
            {
                "algorithm": "product-workspace-source-v1",
                "catalogs": [
                    {
                        "dataset_id": c.dataset_id,
                        "source_sha256": c.source_sha256,
                        "encoding": c.encoding,
                        "rows": len(c.records),
                    }
                    for c in (a, b)
                ],
                "alias_digest": alias_digest,
                "blocking": asdict(policy),
            }
        )
    ).hexdigest()
    namespace = "s4-" + digest
    reference = ArtifactRef(
        artifact_id=namespace, artifact_sha256=digest, profile_id="product-workspace-read-v1"
    )
    access = _WorkspaceRead(namespace, rows, reference)
    registry = Registry()
    install_product_rows(
        registry,
        ProductRowResolver(
            namespace,
            ((dataset, raw) for (dataset, _), raw in rows.items()),
            access=access,
            generations=access,
        ),
    )
    context = ResolutionContext(
        namespace_id=namespace,
        access_policy_ref=reference,
        generation_policy_ref=reference,
        required_scope="text",
    )
    return ComparisonWorkspace(
        namespace, (a, b), batch, alias_digest, rows, index, registry, context
    )
