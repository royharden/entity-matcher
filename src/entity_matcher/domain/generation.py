"""Raw source freshness for the unchanged S2 journal, independent of model processing."""

from collections.abc import Callable, Mapping

from ..shared.match.models import PairIdentity, RawProduct, RecordRef
from ..shared.rulings.models import PairGeneration, PairSubject
from .bindings import ruling_binding

RawInventory = Mapping[tuple[str, str], RawProduct]


class ProductGenerationValidator:
    def __init__(self, current_inventory: Callable[[], RawInventory]):
        self._current = current_inventory

    def validate(self, subject, generation) -> None:
        if type(subject) is not PairSubject or type(generation) is not PairGeneration:
            raise ValueError("product_pair_required")
        # JSON roundtrip revalidates copied owner instances, including unchecked model_copy values.
        subject = PairSubject.model_validate_json(subject.model_dump_json())
        generation = PairGeneration.model_validate_json(generation.model_dump_json())
        if generation.profile != "product-raw-fields-v1":
            raise ValueError("unsupported_raw_profile")
        inventory = self._current()
        refs = []
        for ref in subject.refs:
            raw = inventory.get((ref.dataset_id, ref.record_id))
            if raw is None:
                raise ValueError("source_missing_or_tombstoned")
            raw = RawProduct.model_validate(raw)
            if raw.id != ref.record_id:
                raise ValueError("source_identity_mismatch")
            refs.append(RecordRef.from_raw(ref.dataset_id, raw))
        expected_subject, expected_generation = ruling_binding(PairIdentity.from_refs(*refs))
        if (subject, generation) != (expected_subject, expected_generation):
            raise ValueError("current_raw_generation_mismatch")
