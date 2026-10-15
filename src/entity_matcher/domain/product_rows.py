"""Exact retained raw-field text resolution; no filesystem, URL, semantic or human proof."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from ..shared.cited.models import ArtifactRef, ProductPointer, Resolution, ResolutionContext
from ..shared.cited.registry import Registry
from ..shared.match.models import RawProduct, RecordRef
from .bindings import product_pointer

PRODUCT_KEY = ("row:id", "product_catalog", "record_field")


class ProductAccess(Protocol):
    reference: ArtifactRef

    def decision(self, namespace: str, dataset_id: str, record_id: str) -> str: ...


@dataclass(frozen=True)
class CurrentProduct:
    status: Literal["current", "tombstoned", "unknown"]
    raw: RawProduct | None = None

    def __post_init__(self):
        if self.status not in ("current", "tombstoned", "unknown") or (
            (self.status == "current") != isinstance(self.raw, RawProduct)
        ):
            raise ValueError("invalid_current_product_state")


class ProductGenerations(Protocol):
    reference: ArtifactRef

    def current(self, namespace: str, dataset_id: str, record_id: str) -> CurrentProduct: ...


class ProductRowResolver:
    def __init__(
        self,
        namespace: str,
        retained: Iterable[tuple[str, RawProduct]],
        *,
        access: ProductAccess,
        generations: ProductGenerations,
        max_records: int = 20000,
        max_bytes: int = 40 * 1024 * 1024,
    ):
        if (
            type(max_records) is not int
            or not 1 <= max_records <= 20000
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= 40 * 1024 * 1024
        ):
            raise ValueError("invalid_retained_product_bounds")
        self.namespace, self.access, self.generations = namespace, access, generations
        self._rows = {}
        retained_bytes = 0
        for dataset_id, raw in retained:
            raw = RawProduct.model_validate(raw)
            RecordRef.from_raw(dataset_id, raw)
            key = (dataset_id, raw.id)
            retained_bytes += sum(len(value.encode("utf-8")) for value in raw.model_dump().values())
            if len(self._rows) >= max_records or key in self._rows or retained_bytes > max_bytes:
                raise ValueError("retained_product_bound_or_duplicate")
            self._rows[key] = (raw, raw.fingerprint)

    def resolve(self, pointer, context) -> Resolution:
        def fail(status, reason):
            return Resolution(status=status, reason=reason)

        if type(pointer) is not ProductPointer:
            return fail("unsupported", "wrong_product_domain")
        try:
            pointer = ProductPointer.model_validate_json(pointer.model_dump_json())
            context = ResolutionContext.model_validate_json(context.model_dump_json())
            if (
                context.namespace_id != self.namespace
                or context.access_policy_ref != self.access.reference
                or context.generation_policy_ref != self.generations.reference
            ):
                return fail("unsupported", "product_policy_context_mismatch")
            if context.required_scope not in (None, "text"):
                return fail("unsupported", "required_scope_unavailable")
            dataset, record = pointer.locator.dataset_id, pointer.locator.record_id

            def check_current():
                if self.access.decision(self.namespace, dataset, record) != "allow":
                    return fail("unsupported", "access_not_allowed")
                current = self.generations.current(self.namespace, dataset, record)
                if type(current) is not CurrentProduct or current.status == "unknown":
                    return fail("unsupported", "current_generation_unknown")
                if current.status == "tombstoned":
                    return fail("missing", "source_missing_or_tombstoned")
                raw = RawProduct.model_validate(current.raw)
                if raw.id != record:
                    return fail("invalid", "current_source_identity_mismatch")
                if raw.fingerprint != pointer.source_sha256:
                    return fail("stale", "current_generation_changed")
                return None

            issue = check_current()
            if issue is not None:
                return issue
            retained = self._rows.get((dataset, record))
            if retained is None:
                return fail("missing", "retained_source_missing")
            raw, stored_digest = retained
            raw = RawProduct.model_validate(raw)
            if raw.fingerprint != stored_digest or stored_digest != pointer.source_sha256:
                return fail("invalid", "retained_source_mismatch")
            expected = product_pointer(dataset, raw, pointer.locator.field_id)
            if expected is None or expected.model_dump(
                exclude={"provenance_ref"}
            ) != pointer.model_dump(exclude={"provenance_ref"}):
                return fail("invalid", "raw_field_evidence_mismatch")
            issue = check_current()
            if issue is not None:
                return issue
            if self._rows[(dataset, record)][0].fingerprint != stored_digest:
                return fail("invalid", "retained_source_changed")
            return Resolution(
                status="resolved",
                checked_source_revision=raw.generation,
                checked_source_sha256=stored_digest,
                checked_artifact_sha256=pointer.evidence_artifact_sha256,
                scope="text",
                excerpt=pointer.locator.quote,
                excerpt_sha256=pointer.locator.excerpt_sha256,
                reason="exact_raw_field_checked",
            )
        except Exception:
            return fail("invalid", "product_resolver_error")


def install_product_rows(registry: Registry, resolver: ProductRowResolver) -> None:
    registry.register(PRODUCT_KEY, resolver)
