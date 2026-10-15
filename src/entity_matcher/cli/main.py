"""Bounded catalog inspection and explicit local provisional journal review."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from ..api.fastapi_app import APICapabilityUnavailable
from ..api.launcher import APILaunchError, policy_from_environment, run_server
from ..io.catalog import MAX_FILE_BYTES, CatalogValidation, validate_catalog
from ..pipeline.apply_draft import DraftApplyError
from ..pipeline.review_page import ReviewPageError, encode_review_page, make_review_page
from ..pipeline.ruling_packet import RulingPacketError
from ..pipeline.workspace import WorkspaceError, create_workspace
from ..shared.match.aliases import MAX_ALIAS_BYTES, AliasError, load_aliases
from ..shared.match.blocking import BlockingPolicy
from ..shared.match.candidates import generate_candidates
from ..shared.match.normalize import normalize_record
from ..shared.rulings.store import JournalError
from .review import COMMANDS, register_commands, run_command

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
        "status": "local-provisional-review-source",
        "matching_models_available": True,
        "candidate_inspection_available": True,
        "matching_available": False,
        "read_only_commands": ["validate", "candidates", "review-page"],
        "review_page_export_available": True,
        "calibrated_matching_available": False,
        "human_rulings_available": False,
        "provisional_rulings_available": True,
        "provisional_commands": list(COMMANDS),
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
    review = commands.add_parser(
        "review-page", help="Export a nonauthorizing raw-record review page"
    )
    for command in (candidates, review):
        command.add_argument("a", type=Path)
        command.add_argument("b", type=Path)
        command.add_argument("--dataset-a", default="a")
        command.add_argument("--dataset-b", default="b")
        command.add_argument("--encoding-a")
        command.add_argument("--encoding-b")
        command.add_argument("--aliases", type=Path)
        command.add_argument("--per-a-cap", type=int, default=50)
        command.add_argument("--global-cap", type=int, default=500_000)
    candidates.add_argument("--max-output", type=int, default=100)
    review.add_argument("--method", choices=("rules", "fuzzy"), default="rules")
    review.add_argument("--offset", type=int, default=0)
    review.add_argument("--limit", type=int, default=50)
    serve = commands.add_parser("serve", help="Serve the optional loopback comparison API")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--frontend-port", type=int, action="append", default=[])
    serve.add_argument("--max-requests", type=int, help="Stop after 1..10000 HTTP requests")
    register_commands(commands)
    args = parser.parse_args(argv)
    code = 0
    try:
        if args.command == "serve":
            policy = policy_from_environment(args.port, tuple(args.frontend_port), os.environ)
            if args.max_requests is not None and not 1 <= args.max_requests <= 10000:
                raise APILaunchError("api_launch_configuration_invalid")
            print(
                json.dumps(
                    {"stage": "serve", "status": "starting", "bind": "127.0.0.1", "port": args.port}
                ),
                flush=True,
            )
            output = run_server(policy, max_requests=args.max_requests)
        elif args.command in COMMANDS:
            try:
                encoded, code = run_command(
                    args, read_file=read_explicit_file, protected_parts=PROTECTED_PARTS
                )
            except InputFailure:
                raise
            except (DraftApplyError, RulingPacketError, WorkspaceError, JournalError) as error:
                output = {"stage": args.command, "status": "invalid", "issue_code": str(error)}
                print(json.dumps(output, sort_keys=True))
                return 2
            except ValueError:
                output = {
                    "stage": args.command,
                    "status": "invalid",
                    "issue_code": "review_request_invalid",
                }
                print(json.dumps(output, sort_keys=True))
                return 2
            buffer = getattr(sys.stdout, "buffer", None)
            if buffer is None:
                sys.stdout.write(encoded.decode("utf-8"))
            else:
                buffer.write(encoded)
            return code
        elif args.command is None:
            output = stage_state()
        elif args.command == "validate":
            result = validate_catalog(
                read_explicit_file(args.file, MAX_FILE_BYTES), args.dataset, encoding=args.encoding
            )
            output = {"stage": "validation", **validation_view(result)}
            code = 0 if result.accepted else 2
        elif args.command == "review-page":
            if args.dataset_a == args.dataset_b:
                raise InputFailure("dataset_namespaces_must_differ")
            if not 1 <= args.limit <= 100 or not 0 <= args.offset <= 500000:
                raise InputFailure("workspace_page_bound")
            try:
                policy = BlockingPolicy(per_a_cap=args.per_a_cap, global_cap=args.global_cap)
            except ValueError:
                raise InputFailure("blocking_policy_bound_invalid") from None
            workspace = create_workspace(
                read_explicit_file(args.a, MAX_FILE_BYTES),
                read_explicit_file(args.b, MAX_FILE_BYTES),
                dataset_a=args.dataset_a,
                dataset_b=args.dataset_b,
                encoding_a=args.encoding_a,
                encoding_b=args.encoding_b,
                aliases_csv=read_explicit_file(args.aliases, MAX_ALIAS_BYTES)
                if args.aliases
                else None,
                policy=policy,
            )
            encoded = encode_review_page(
                make_review_page(
                    workspace, method=args.method, offset=args.offset, limit=args.limit
                )
            )
            # Finish validation and the full byte bound before writing any output bytes.
            buffer = getattr(sys.stdout, "buffer", None)
            if buffer is None:
                sys.stdout.write(encoded.decode("utf-8"))
            else:
                buffer.write(encoded)
            return 0
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
    except (APILaunchError, APICapabilityUnavailable) as error:
        output = {"stage": args.command, "status": "unavailable", "issue_code": str(error)}
    except AliasError:
        output = {"stage": args.command, "status": "invalid", "issue_code": "invalid_alias_table"}
    except (WorkspaceError, ReviewPageError) as error:
        output = {"stage": args.command, "status": "invalid", "issue_code": str(error)}
    print(json.dumps(output, sort_keys=True))
    return 2
