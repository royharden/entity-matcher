"""Bounded explicit-file validation and candidate inspection with zero write/model effects."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from ..io.catalog import MAX_FILE_BYTES, CatalogValidation, validate_catalog
from ..shared.match.aliases import MAX_ALIAS_BYTES, AliasError, load_aliases
from ..shared.match.blocking import BlockingPolicy
from ..shared.match.candidates import generate_candidates
from ..shared.match.normalize import normalize_record

PROTECTED_PARTS = {".creds", ".thinking", ".agent-registry", ".agent-work", ".chatexports", ".git"}


class InputFailure(ValueError):
    """A code-only local file finding, without source values or secret paths."""


def read_explicit_file(path: Path, maximum: int) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        if any(part.casefold() in PROTECTED_PARTS for part in resolved.parts):
            raise InputFailure("protected_input_path")
        if resolved.name.casefold().startswith(".env"):
            raise InputFailure("protected_input_path")
        if not resolved.is_file():
            raise InputFailure("input_not_file")
        if resolved.stat().st_size > maximum:
            raise InputFailure("file_too_large")
        with resolved.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise InputFailure("file_too_large")
        return data
    except OSError as error:
        raise InputFailure("input_unreadable") from error


def validation_view(result: CatalogValidation) -> dict:
    return {
        "dataset_id": result.dataset_id,
        "accepted": result.accepted,
        "source_sha256": result.source_sha256,
        "encoding": result.encoding,
        "data_rows_seen": result.data_rows_seen,
        "row_count_complete": result.row_count_complete,
        "accepted_record_count": len(result.records),
        "issues": [asdict(issue) for issue in result.issues],
    }


def stage_state() -> dict:
    return {
        "application": "entity-matcher",
        "status": "read-only-source",
        "matching_models_available": True,
        "candidate_inspection_available": True,
        "matching_available": False,
        "read_only_commands": ["validate", "candidates"],
        "calibrated_matching_available": False,
        "human_rulings_available": False,
        "interactive_review_available": False,
        "next_step": "See README.md and docs/LIMITATIONS.md for implemented and pending features.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    validation = commands.add_parser("validate", help="Validate one exact eight-column CSV")
    validation.add_argument("file", type=Path)
    validation.add_argument("--dataset", required=True)
    validation.add_argument("--encoding")
    candidates = commands.add_parser("candidates", help="Inspect bounded proposed candidates only")
    candidates.add_argument("a", type=Path)
    candidates.add_argument("b", type=Path)
    candidates.add_argument("--dataset-a", default="a")
    candidates.add_argument("--dataset-b", default="b")
    candidates.add_argument("--encoding-a")
    candidates.add_argument("--encoding-b")
    candidates.add_argument("--aliases", type=Path)
    candidates.add_argument("--per-a-cap", type=int, default=50)
    candidates.add_argument("--global-cap", type=int, default=500_000)
    candidates.add_argument("--max-output", type=int, default=100)
    args = parser.parse_args(argv)
    code = 0
    try:
        if args.command is None:
            output = stage_state()
        elif args.command == "validate":
            result = validate_catalog(
                read_explicit_file(args.file, MAX_FILE_BYTES), args.dataset, encoding=args.encoding
            )
            output = {"stage": "validation", **validation_view(result)}
            code = 0 if result.accepted else 2
        else:
            if args.dataset_a == args.dataset_b:
                raise InputFailure("dataset_namespaces_must_differ")
            if not 1 <= args.max_output <= 1000:
                raise InputFailure("candidate_preview_bound_invalid")
            try:
                policy = BlockingPolicy(per_a_cap=args.per_a_cap, global_cap=args.global_cap)
            except ValueError as error:
                raise InputFailure("blocking_policy_bound_invalid") from error
            a = validate_catalog(
                read_explicit_file(args.a, MAX_FILE_BYTES), args.dataset_a, encoding=args.encoding_a
            )
            b = validate_catalog(
                read_explicit_file(args.b, MAX_FILE_BYTES), args.dataset_b, encoding=args.encoding_b
            )
            output = {
                "stage": "candidates",
                "validation": [validation_view(a), validation_view(b)],
                "judgment_performed": False,
                "human_rulings_applied": False,
            }
            if not a.accepted or not b.accepted:
                code = 2
            else:
                aliases = (
                    load_aliases(read_explicit_file(args.aliases, MAX_ALIAS_BYTES))
                    if args.aliases
                    else None
                )
                left = tuple(
                    normalize_record(raw, args.dataset_a, aliases=aliases) for raw in a.records
                )
                right = tuple(
                    normalize_record(raw, args.dataset_b, aliases=aliases) for raw in b.records
                )
                batch = generate_candidates(left, right, policy=policy)
                output["report"] = asdict(batch.report)
                output["alias_digest"] = aliases.digest if aliases else None
                output["candidate_preview"] = [
                    {
                        "refs": [ref.model_dump() for ref in pair.refs],
                        "pair_key": pair.pair_key,
                        "pair_generation": pair.pair_generation,
                        "blockers": pair.blockers,
                        "blocking_overlap": pair.blocking_overlap,
                    }
                    for pair in batch.candidates[: args.max_output]
                ]
                output["preview_truncated"] = len(batch.candidates) > args.max_output
        print(json.dumps(output, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return code
    except InputFailure as error:
        output = {"stage": args.command, "status": "invalid", "issue_code": str(error)}
    except AliasError:
        output = {"stage": args.command, "status": "invalid", "issue_code": "invalid_alias_table"}
    print(json.dumps(output, sort_keys=True))
    return 2
