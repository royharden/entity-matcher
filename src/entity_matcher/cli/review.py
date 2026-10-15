"""Explicit local provisional review commands over the actual owner journal."""

import json
from dataclasses import asdict
from pathlib import Path

from ..domain.components import project_components
from ..export.csv_views import component_csv, pair_csv
from ..io.catalog import MAX_FILE_BYTES
from ..pipeline.apply_draft import DraftApplyError, apply_draft
from ..pipeline.ruling_packet import (
    MAX_PACKET_BYTES,
    decode_draft,
    decode_packet,
    encode_packet,
    make_ruling_packet,
)
from ..pipeline.workspace import create_workspace
from ..shared.match.aliases import MAX_ALIAS_BYTES
from ..shared.match.blocking import BlockingPolicy
from ..shared.rulings.store import JournalStore

COMMANDS = ("packet", "apply-rulings", "export")


def register_commands(commands):
    parsers = []
    for name in COMMANDS:
        parser = commands.add_parser(name, help="Local provisional review; no human admission")
        for side in ("a", "b"):
            parser.add_argument(side, type=Path)
            parser.add_argument(f"--dataset-{side}", default=side)
            parser.add_argument(f"--encoding-{side}")
        parser.add_argument("--aliases", type=Path)
        parser.add_argument("--per-a-cap", type=int, default=50)
        parser.add_argument("--global-cap", type=int, default=500000)
        parser.add_argument("--journal-dir", type=Path, required=True)
        parser.add_argument("--namespace", required=True)
        parsers.append(parser)
    packet, apply, export = parsers
    packet.add_argument("--method", choices=("rules", "fuzzy"), default="rules")
    packet.add_argument("--offset", type=int, default=0)
    packet.add_argument("--limit", type=int, default=50)
    apply.add_argument("--packet", type=Path, required=True)
    apply.add_argument("--draft", type=Path, required=True)
    export.add_argument("--view", choices=("pairs", "components"), default="pairs")


def _journal_root(path, protected_parts):
    def protected(candidate):
        return any(
            part.casefold() in protected_parts or part.casefold().startswith(".env")
            for part in candidate.parts
        )

    if protected(path):
        raise DraftApplyError("protected_journal_path")
    try:
        resolved = path.resolve(strict=True)
        if protected(resolved):
            raise DraftApplyError("protected_journal_path")
        if not resolved.is_dir():
            raise DraftApplyError("journal_directory_required")
        # Keep lexical spelling: the actual owner store rejects static reparse/symlink roots.
        return path.absolute()
    except OSError:
        raise DraftApplyError("journal_directory_unavailable") from None


def run_command(args, *, read_file, protected_parts):
    if args.dataset_a == args.dataset_b:
        raise DraftApplyError("dataset_namespaces_must_differ")
    if type(args.namespace) is not str or not 1 <= len(args.namespace) <= 512:
        raise DraftApplyError("journal_namespace_bound")
    root = _journal_root(args.journal_dir, protected_parts)
    if args.command == "packet" and (not 1 <= args.limit <= 100 or not 0 <= args.offset <= 500000):
        raise DraftApplyError("workspace_page_bound")
    policy = BlockingPolicy(per_a_cap=args.per_a_cap, global_cap=args.global_cap)
    workspace = create_workspace(
        read_file(args.a, MAX_FILE_BYTES),
        read_file(args.b, MAX_FILE_BYTES),
        dataset_a=args.dataset_a,
        dataset_b=args.dataset_b,
        encoding_a=args.encoding_a,
        encoding_b=args.encoding_b,
        aliases_csv=read_file(args.aliases, MAX_ALIAS_BYTES) if args.aliases else None,
        policy=policy,
    )
    # Decode every explicit transport input before owner journal activity.
    if args.command == "apply-rulings":
        packet = decode_packet(read_file(args.packet, MAX_PACKET_BYTES))
        draft = decode_draft(read_file(args.draft, MAX_PACKET_BYTES))
    store = JournalStore(root)
    if args.command == "packet":
        packet = make_ruling_packet(
            workspace,
            store.replay(),
            namespace=args.namespace,
            method=args.method,
            offset=args.offset,
            limit=args.limit,
        )
        return encode_packet(packet), 0
    if args.command == "apply-rulings":
        result = apply_draft(workspace, packet, draft, store, namespace=args.namespace)
        output = asdict(result)
        output["receipts"] = [receipt.model_dump(mode="json") for receipt in result.receipts]
        output["stage"] = "provisional_ruling_apply"
        output["status"] = "complete" if result.issue_code is None else "stopped"
        return json.dumps(output, ensure_ascii=True, sort_keys=True).encode(), (
            0 if result.issue_code is None else 3
        )
    snapshot = store.replay()
    if not snapshot.can_append:
        raise DraftApplyError("journal_read_only")
    projection = project_components(
        snapshot,
        workspace.inventory,
        namespace=args.namespace,
        candidate_pair_keys=frozenset(p.pair_key for p in workspace.batch.candidates),
    )
    view = pair_csv(projection) if args.view == "pairs" else component_csv(projection)
    return view.data, 0
