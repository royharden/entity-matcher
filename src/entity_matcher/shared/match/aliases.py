"""Bounded editable brand aliases with normalized conflicts/cycles rejected."""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .identity import canonical_bytes
from .normalize import normalize_text

MAX_ALIAS_BYTES = 1024 * 1024
MAX_ALIASES = 10_000


class AliasError(ValueError):
    """Messages exclude source alias values."""


@dataclass(frozen=True)
class AliasTable:
    mapping: Mapping[str, str]
    digest: str
    profile: str = "brand-aliases-v1"

    def resolve(self, brand: str) -> str:
        normalized = normalize_text(brand)
        return self.mapping.get(normalized, normalized)


def load_aliases(data: bytes) -> AliasTable:
    if type(data) is not bytes or len(data) > MAX_ALIAS_BYTES:
        raise AliasError("Invalid or oversized alias input")
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        if next(reader, None) != ["alias", "canonical"]:
            raise AliasError("Invalid alias header")
        direct: dict[str, str] = {}
        count = 0
        for row in reader:
            count += 1
            if (
                count > MAX_ALIASES
                or len(row) != 2
                or any(len(cell.encode()) > 8192 for cell in row)
            ):
                raise AliasError("Alias row bounds exceeded")
            alias, target = map(normalize_text, row)
            if not alias or not target:
                raise AliasError("Empty normalized alias")
            if alias in direct and direct[alias] != target:
                raise AliasError("Conflicting normalized alias")
            if alias == target:
                raise AliasError("Self-cycle in normalized alias")
            direct[alias] = target
    except (UnicodeError, csv.Error) as error:
        raise AliasError("Invalid alias encoding or CSV") from error
    resolved: dict[str, str] = {}
    for alias in sorted(direct):
        path: list[str] = []
        visited: set[str] = set()
        current = alias
        while current in direct and current not in resolved:
            if current in visited:
                raise AliasError("Cycle in normalized aliases")
            visited.add(current)
            path.append(current)
            current = direct[current]
        terminal = resolved.get(current, current)
        for item in path:
            resolved[item] = terminal
    digest = hashlib.sha256(
        canonical_bytes({"profile": "brand-aliases-v1", "mapping": resolved})
    ).hexdigest()
    return AliasTable(MappingProxyType(resolved), digest)
