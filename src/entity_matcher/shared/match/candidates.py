"""Deterministic bounded union and caps, with explicit exact-versus-bound accounting."""

from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass

from .blocking import BlockingPolicy, BlockOmission, build_index, name_keys, record_key, size_keys
from .identity import canonical_bytes, pair_generation, pair_key
from .models import RecordRef
from .normalize import NormalizedRecord

MAX_CATALOG_ROWS = 10_000


@dataclass(frozen=True)
class CandidatePair:
    refs: tuple[RecordRef, RecordRef]
    pair_key: str
    pair_generation: str
    blockers: tuple[str, ...]
    blocking_overlap: int


@dataclass(frozen=True)
class RowCap:
    identity: tuple[str, str]
    considered: int
    retained_before_global_cap: int
    cap_dropped_count: int
    cap_drop_identity_sha256: str | None


@dataclass(frozen=True)
class CandidateReport:
    profile: str
    policy: BlockingPolicy
    n_a: int
    n_b: int
    possible_pairs: int
    b1_enumerated_unique: int
    b2_enumerated_unique: int
    union_enumerated_unique: int
    uncapped_union_lower_bound: int
    uncapped_union_upper_bound: int
    uncapped_union_exact: bool
    retained: int
    per_a_cap_drops: int
    global_cap_drops: int
    global_cap_drop_identity_sha256: str | None
    token_keys_truncated_records: int
    block_omissions: tuple[BlockOmission, ...]
    row_caps: tuple[RowCap, ...]

    @property
    def reduction(self) -> float | None:
        return None if not self.possible_pairs else 1 - self.retained / self.possible_pairs


@dataclass(frozen=True)
class CandidateBatch:
    candidates: tuple[CandidatePair, ...]
    report: CandidateReport


@dataclass
class _HeapItem:
    rank: tuple[int, bytes, bytes]
    left: NormalizedRecord
    right: NormalizedRecord
    blockers: tuple[str, ...]

    def __lt__(self, other: _HeapItem) -> bool:
        # Reversed rank keeps the worst retained entry at heap root.
        return self.rank > other.rank


def _validate_catalog(records: tuple[NormalizedRecord, ...]) -> set[bytes]:
    if len(records) > MAX_CATALOG_ROWS:
        raise ValueError("Candidate catalog row limit exceeded")
    keys = {record_key(record) for record in records}
    if len(keys) != len(records):
        raise ValueError("Duplicate stable identities in candidate catalog")
    return keys


def generate_candidates(
    left: tuple[NormalizedRecord, ...],
    right: tuple[NormalizedRecord, ...],
    *,
    policy: BlockingPolicy | None = None,
) -> CandidateBatch:
    """Memory scales with records, bounded postings, per-row union and retained heap.

    Counts concern supplied normalized input, never Gold. Unenumerated pruning/refinement
    uses conservative bounds, not invented exact original union counts. Per-A cap drop
    digest is SHA256 of ordered canonical two-identity JSON arrays, each followed by LF
    (candidate-drop-jsonl-v1); it permits replay without retaining a large dropped matrix.
    Global drop digest uses the same bytes, in deterministic heap-replacement/reject order.
    """
    policy = policy or BlockingPolicy()
    if _validate_catalog(left) & _validate_catalog(right):
        raise ValueError("Input sides must have distinct namespaced identities")
    ordered_left = sorted(left, key=record_key)
    index = build_index(right, policy)
    heap: list[_HeapItem] = []
    global_drop_hash = hashlib.sha256()
    global_drops = 0

    def record_global_drop(item: _HeapItem) -> None:
        nonlocal global_drops
        refs = sorted(
            (item.left.ref.identity_fields(), item.right.ref.identity_fields()), key=canonical_bytes
        )
        global_drop_hash.update(canonical_bytes(refs) + b"\n")
        global_drops += 1

    row_caps: list[RowCap] = []
    b1_count = b2_count = union_count = per_a_drops = offered = 0
    truncated = sum(
        record.tokens_truncated or len(record.name_tokens) > policy.token_keys_cap
        for record in (*left, *right)
    )
    for a in ordered_left:
        a_key = record_key(a)
        # Each row owns only the bounded posting union; no Cartesian pair allocation.
        matches: dict[bytes, tuple[NormalizedRecord, set[str], int]] = {}
        for blocker, keys, postings in (
            ("B1", name_keys(a, policy), index.b1),
            ("B2", size_keys(a, neighbors=True), index.b2),
        ):
            for key in keys:
                for b in postings.get(key, ()):
                    b_key = record_key(b)
                    prior = matches.get(b_key)
                    if prior is None:
                        matches[b_key] = (b, {blocker}, 1)
                    else:
                        matches[b_key] = (b, prior[1] | {blocker}, prior[2] + 1)
        b1_count += sum("B1" in match[1] for match in matches.values())
        b2_count += sum("B2" in match[1] for match in matches.values())
        union_count += len(matches)
        scored: list[_HeapItem] = []
        for b_key, (b, blockers, overlap) in matches.items():
            lo, hi = sorted((a_key, b_key))
            scored.append(_HeapItem((-overlap, lo, hi), a, b, tuple(sorted(blockers))))
        scored.sort(key=lambda item: item.rank)
        selected, dropped = scored[: policy.per_a_cap], scored[policy.per_a_cap :]
        dropped_hash = hashlib.sha256()
        for item in dropped:
            ordered_refs = sorted(
                (item.left.ref.identity_fields(), item.right.ref.identity_fields()),
                key=canonical_bytes,
            )
            dropped_hash.update(canonical_bytes(ordered_refs) + b"\n")
        per_a_drops += len(dropped)
        row_caps.append(
            RowCap(
                (a.ref.dataset_id, a.ref.record_id),
                len(scored),
                len(selected),
                len(dropped),
                dropped_hash.hexdigest() if dropped else None,
            )
        )
        for item in selected:
            offered += 1
            if len(heap) < policy.global_cap:
                heapq.heappush(heap, item)
            elif item.rank < heap[0].rank:
                record_global_drop(heapq.heapreplace(heap, item))
            else:
                record_global_drop(item)
    candidates: list[CandidatePair] = []
    for item in sorted(heap, key=lambda entry: entry.rank):
        refs = tuple(
            sorted(
                (item.left.ref, item.right.ref),
                key=lambda ref: canonical_bytes(ref.identity_fields()),
            )
        )
        lo, hi = refs
        candidates.append(
            CandidatePair(
                refs,
                pair_key(lo.identity_fields(), hi.identity_fields()),
                pair_generation(
                    lo.identity_fields(), lo.content_hash, hi.identity_fields(), hi.content_hash
                ),
                item.blockers,
                -item.rank[0],
            )
        )
    possible = len(left) * len(right)
    exact = not index.omissions and not truncated
    report = CandidateReport(
        "product-candidates-v1",
        policy,
        len(left),
        len(right),
        possible,
        b1_count,
        b2_count,
        union_count,
        union_count,
        union_count if exact else possible,
        exact,
        len(candidates),
        per_a_drops,
        offered - len(candidates),
        global_drop_hash.hexdigest() if global_drops else None,
        truncated,
        index.omissions,
        tuple(row_caps),
    )
    return CandidateBatch(tuple(candidates), report)
