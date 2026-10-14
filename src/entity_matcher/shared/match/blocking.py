"""Two bounded inverted indexes; blocking is candidate retrieval, never equality."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .identity import canonical_bytes
from .normalize import NormalizedRecord

BlockKey = tuple[str, ...]


def record_key(record: NormalizedRecord) -> bytes:
    return canonical_bytes(record.ref.identity_fields())


@dataclass(frozen=True)
class BlockingPolicy:
    per_a_cap: int = 50
    global_cap: int = 500_000
    posting_cap: int = 128
    token_frequency_cap: int = 128
    token_keys_cap: int = 16
    name_prefix_chars: int = 16
    profile: str = "product-blocking-v1"

    def __post_init__(self) -> None:
        limits = {
            "per_a_cap": 50,
            "global_cap": 500_000,
            "posting_cap": 256,
            "token_frequency_cap": 256,
            "token_keys_cap": 64,
            "name_prefix_chars": 64,
        }
        for name, maximum in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid blocking policy bound")
        if self.profile != "product-blocking-v1":
            raise ValueError("Unsupported blocking profile")


def name_keys(record: NormalizedRecord, policy: BlockingPolicy) -> tuple[BlockKey, ...]:
    keys: list[BlockKey] = []
    if record.name:
        keys.append(("prefix", record.name[: policy.name_prefix_chars]))
    keys.extend(
        ("token", token) for token in record.name_tokens[: policy.token_keys_cap] if len(token) >= 2
    )
    return tuple(keys)


def quantity_bucket(record: NormalizedRecord) -> int | None:
    quantity = record.quantity
    if not record.brand or quantity.status != "known" or quantity.per_pack_base is None:
        return None
    # Geometric 2%-relative bins, with +/-2 query neighbors to cover boundaries
    # and finite floating representation. Exact dimensional comparison happens later.
    return math.floor(math.log(float(quantity.per_pack_base)) / math.log(1 / 0.98))


def size_keys(record: NormalizedRecord, *, neighbors: bool) -> tuple[BlockKey, ...]:
    bucket = quantity_bucket(record)
    if bucket is None:
        return ()
    q = record.quantity
    offsets = range(-2, 3) if neighbors else (0,)
    return tuple(
        (record.brand, str(q.dimension), str(q.pack_count), str(bucket + offset))
        for offset in offsets
    )


@dataclass(frozen=True)
class BlockOmission:
    blocker: str
    key_sha256: str
    reason: str
    original_posting_count: int
    retained_posting_count: int


@dataclass(frozen=True)
class BlockingIndex:
    b1: Mapping[BlockKey, tuple[NormalizedRecord, ...]]
    b2: Mapping[BlockKey, tuple[NormalizedRecord, ...]]
    omissions: tuple[BlockOmission, ...]
    policy: BlockingPolicy


def _refine(
    records: list[NormalizedRecord], policy: BlockingPolicy
) -> tuple[NormalizedRecord, ...]:
    """Round-robin name-prefix partitions; selection is deterministic and disclosed."""
    if len(records) <= policy.posting_cap:
        return tuple(sorted(records, key=record_key))
    groups: dict[str, list[NormalizedRecord]] = defaultdict(list)
    for record in records:
        groups[record.name[: policy.name_prefix_chars]].append(record)
    ordered = [sorted(groups[key], key=record_key) for key in sorted(groups)]
    retained: list[NormalizedRecord] = []
    position = 0
    while len(retained) < policy.posting_cap:
        for group in ordered:
            if position < len(group):
                retained.append(group[position])
                if len(retained) == policy.posting_cap:
                    break
        position += 1
    return tuple(sorted(retained, key=record_key))


def build_index(records: tuple[NormalizedRecord, ...], policy: BlockingPolicy) -> BlockingIndex:
    b1: dict[BlockKey, list[NormalizedRecord]] = defaultdict(list)
    b2: dict[BlockKey, list[NormalizedRecord]] = defaultdict(list)
    for record in records:
        for key in name_keys(record, policy):
            b1[key].append(record)
        for key in size_keys(record, neighbors=False):
            b2[key].append(record)
    omissions: list[BlockOmission] = []

    def finish(blocker: str, index: dict[BlockKey, list[NormalizedRecord]]) -> Mapping:
        result = {}
        for key in sorted(index):
            posting = index[key]
            digest = hashlib.sha256(canonical_bytes(list(key))).hexdigest()
            if blocker == "B1" and key[0] == "token" and len(posting) > policy.token_frequency_cap:
                omissions.append(
                    BlockOmission(blocker, digest, "high_frequency_token", len(posting), 0)
                )
                continue
            selected = _refine(posting, policy)
            if len(selected) < len(posting):
                omissions.append(
                    BlockOmission(
                        blocker, digest, "posting_refined_and_capped", len(posting), len(selected)
                    )
                )
            result[key] = selected
        return MappingProxyType(result)

    left, right = finish("B1", b1), finish("B2", b2)
    return BlockingIndex(left, right, tuple(omissions), policy)
