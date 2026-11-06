# Product instructions v3

This inner repository contains the product only. Agent coordination, registry, plans, receipts and chat exports belong in the parent shell and are not runtime dependencies. Keep instruction files limited to product commands and stable invariants. Canonical local skills and operating grants are referenced by the parent shell; do not copy them into this public product repository.

Current scope includes strict matching models/identity, bounded CSV validation, separate normalization/quantity/aliases, capped candidate retrieval and raw-field evidence checks. Offline rule constraints are implemented; fuzzy scoring requires its declared RapidFuzz dependency. A complete comparison workflow, fitted calibration, generic human-ruling persistence, provider integration and interactive review are not implemented.

Read-only CLI: python -m entity_matcher validate <file.csv> --dataset <namespace>; python -m entity_matcher candidates <a.csv> <b.csv>. Commands print bounded inspection JSON without writing source records, judging matches or creating human decisions. No implicit provider, listener or journal operation.

Use Python3.12 and uv from the repository root: uv sync --frozen; uv run ruff check .; uv run ruff format --check .; uv run pytest; uv run python scripts/generate_match_schemas.py --check; uv run python scripts/check_public_tree.py; uv run python -m entity_matcher. Frontend: npm ci; npm run typecheck; npm run test:web; npm run build. There is no `lint` script and no `test` script. No command here starts a listener.

The local activation slice is `gdl32-activation-001`. Its report is SoftwareFactory `.agent-work/parts89/GDL32-entity-matcher/REPORT.md`. Hands, Seats, PracticeHub, and SeniorSocial are outside this project. Do not push, change `origin`, or initialize another repository.

Fixtures/gold must be synthetic or verified public data; never commit real customer data, credentials, runtime journals, uploaded inputs, caches, local databases or agent material. Provider secrets stay server-side; no VITE_ secret variables. No automatic source-record changes or model-labelled human rulings. Stable pair identity uses dataset ID and opaque record ID only; raw content generations are separate.

Owner contracts must be adopted before shared implementation or copying. Proposed schema examples are not executable contracts. Never invent source commits or measured model results. Any listener, provider call, child or heavy operation requires its actual resource/grant scope.

Changelog: v3 adds actual read-only CLI commands. v2 updates the implemented source inventory and schema check command. v1 establishes root commands, product-only layout and the scaffold boundary.

<!-- xexport-auto:begin -->
## Chat autosave

Do not copy private transcripts, credentials, or `.chatexports` into this public inner repository. Export at session start, after each completed batch or child handoff, before final reporting, and at the next available execution step when the last verified export is older than 10 minutes. On a user status request, refresh if stale.

    xexport current --source <claude|codex|cursor|grok|antigravity> --session-id "<exact own session id>" \
        --format html --mode append --out "C:\Users\Roy Harden\OneDrive\PJ-OD\SoftwareFactory\.chatexports"

- Session id: `CODEX_THREAD_ID` (Codex), `CURSOR_CONVERSATION_ID` (Cursor),
  `CLAUDE_CODE_SESSION_ID` (Claude Code), `GROK_SESSION_ID` (Grok),
  `ANTIGRAVITY_CONVERSATION_ID` (Antigravity). Do not use recency when an ID is absent.
- Verify the returned session ID on every result, including "Up to date"; exit 0
  alone is insufficient. On missing/mismatched identity, preserve existing exports,
  stop this path, inspect only the intended session metadata, and report the failure.
  Never use `--mode replace` to repair identity.
- Add `--callsign "<your callsign>"` for your own main session if assigned. A callsign
  affects labeling only. Fresh-context child exports use their own opening assignment.
  Full-history child exports preserve and label inherited context; they use neutral
  `Codex_Sub` labeling when the child's assignment cannot be separated from copied history.
- `--mode append` re-parses and re-renders the whole transcript with shrink/fidelity
  guards. It is not literal line appending; unchanged content is a no-op. Preserve
  guard warnings verbatim, protected originals, and any companion's provenance.
- For children, require xexport **0.2.2+** on Codex. Parent-driven export is:
  `xexport subagents --source <claude|codex|cursor|grok|antigravity> --session-id "<exact parent id>" --format html --mode append --out "C:\Users\Roy Harden\OneDrive\PJ-OD\SoftwareFactory\.chatexports"`.
  Codex discovery includes direct children only; run again for each child with
  children of its own. Verify each returned ID against the explicitly linked child.
- Native Codex children may self-export when runtime `CODEX_THREAD_ID` matches
  metadata `payload.id` and the rollout filename. Metadata `session_id` and runtime
  `CODEX_SESSION_ID` can identify the parent. If the runtime ID is inherited/missing,
  have the parent use the verified exact child thread ID. Before 0.2.2, even an exact
  child ID is unsafe: report the unsupported path instead of attempting the write.
- Claude/Cursor children without a verified independent identity rely on their
  parent/hook; do not run `current` with an inherited parent ID and a child's callsign.
- Preserve parent/child last-success times, pending failures, and next freshness
  checkpoint in existing task status/handoff notes across compaction. Update success
  only after verification; report stale/failed children on status requests. This is
  execution policy, not authorization to install a watcher or scheduled automation.
- A receipt path you quote to another agent is a location, not permission to read it:
  transcripts hold the user's prompts and tool output.
- Exports are folders under `.chatexports\html\`. To read one, yours or another
  agent's, follow the `xexport-read` skill: page map in `index.html` first, then the
  `page-NNN.html` files, each sized for one read. Add `--format both` to also keep a
  single-file Markdown copy.
<!-- xexport-auto:end -->

