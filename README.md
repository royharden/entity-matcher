# Entity Matcher

Development is paused. See [the current checkpoint](DEVELOPMENT-STATUS.md) for finished work, work in progress, outstanding tasks and lessons.

Compare two product catalogs and record provisional review decisions locally. CSV inputs remain unchanged; decisions use a separate JSONL journal. Genuine human admission and measured match quality remain unfinished.

The CLI validates catalogs, retrieves bounded candidates, exports raw-field review packets, applies provisional drafts, replays decisions and exports display CSVs. The React viewer source supports packet inspection, decision drafting and file import/download. Browser behavior and its current production build remain unverified. Fuzzy/rules scoring requires RapidFuzz and reports unavailable when absent; unfitted probabilities stay unavailable and proposals stay in review.

## Keyless setup

From the repository root, use Python 3.12 with uv. The smallest runnable workflow needs no frontend or provider key:

    uv sync --frozen
    uv run python scripts/demo.py --out .local/demo-first

Use a new output directory for each run; existing directories refuse without overwriting. The demo authors two tiny synthetic catalogs, calls the actual CLI, appends an explicitly automated provisional decision, retries the same operation, retracts it and exports CSVs. It then edits a separate source copy and demonstrates a refused stale-packet apply with unchanged journal bytes. RESULT.json records actual exits, input preservation, signal availability and artifact hashes. This is a workflow demonstration, not a person review or accuracy evaluation.

The demo leaves current-packet.json, decisions/receipts, pairs.csv, components.csv and its separate journal under the chosen directory. Missing RapidFuzz is reported in the result; no replacement scorer or model call is used. All decisions remain agent_provisional and cannot create verified human match components.

Development checks and the optional frontend use Node 22.12 or newer:

    uv sync --frozen
    uv run python -m entity_matcher
    uv run pytest
    uv run python scripts/generate_match_schemas.py --check
    uv run python scripts/check_public_tree.py
    npm ci
    npm run test:web
    npm run build

Python 3.12 and Node 24 are the local tool versions. The no-argument Python command reports actual provisional capabilities, with human admission disabled. Node transport tests include real Python decoder/journal interoperability and therefore need the Python environment. The frontend build target is dist/; build/browser verification remains pending. These commands start no application listener or provider call.

Inspect explicitly selected local files with:

    uv run python -m entity_matcher validate catalog-a.csv --dataset a
    uv run python -m entity_matcher candidates catalog-a.csv catalog-b.csv --max-output 100

These commands print JSON to standard output and leave source files unchanged. Validation findings contain row/field locations without raw cell values. Candidate previews carry identities/hashes and blocking metadata; they are not judged matches or human decisions. Distinct dataset namespaces are required. Preview truncation is explicit and separate from candidate generation caps. Secret and agent-only paths are refused before reading.

For the frozen synthetic demo, save a report and open it with **Choose report** in the viewer:

    uv run python -m entity_matcher candidates fixtures/independent/demo/catalog-A.csv fixtures/independent/demo/catalog-B.csv --aliases fixtures/independent/brand-aliases.csv --max-output 100 > candidate-preview.json

The viewer shows reported counts, caps, omissions and paged candidate identities. Search covers the imported preview. Candidate reports and review pages are inspection-only; ruling packets enable provisional local drafts. File import does not establish source freshness or authority. Browser interaction and the current viewer's production build still require verification.

To include aligned raw fields, machine proposals and field-evidence metadata, export a review page:

    uv run python -m entity_matcher review-page catalog-a.csv catalog-b.csv --method rules --limit 50 > review-page.json

Open that JSON with the same file chooser. Each file holds at most100 pairs and20MiB of complete JSON. Use the reported next offset to request another page. A byte-limit failure produces an error and no partial page; reduce the limit and retry. Missing scorers and unfitted calibration keep proposals in review. File import grants no human or journal authority.

## File review commands

For your own catalogs, create an owner-controlled journal directory first. Use the same explicit namespace and original packet when applying its downloaded decisions:

    uv run python -m entity_matcher packet catalog-a.csv catalog-b.csv --journal-dir local-journal --namespace my-products > review-packet.json
    uv run python -m entity_matcher apply-rulings catalog-a.csv catalog-b.csv --journal-dir local-journal --namespace my-products --packet review-packet.json --draft entity-matcher-decisions.json
    uv run python -m entity_matcher export catalog-a.csv catalog-b.csv --journal-dir local-journal --namespace my-products --view pairs > pair-review.csv

When the frontend is running, open review-packet.json with Choose report. Match/different/unsure choices and reasons stay in local draft state. Download decisions and apply them with Python; the browser cannot write the journal. Skip/clearing an unsubmitted draft creates no event. Generate a fresh packet before retracting a recorded decision. Existing-head retraction appends a tombstone rather than reviving earlier decisions.

Apply exits 0 for complete acknowledgement, 2 for invalid/stale inputs, and 3 for a stopped or uncertain write. Receipts disclose acknowledged effects and remaining operation IDs; batches do not roll back. Preserve an uncertain draft and retry its exact operation IDs after reconciliation. A historical retry receipt grants no new current authority. Damaged journals refuse new writes and CSV export; no automatic tail repair is supplied.

Packets/drafts contain at most 100 pairs/operations and 20 MiB of JSON. Current source/head checks run in Python. Neither imported claims nor a browser token/name/flag admits a human; that branch is disabled. CSV exports are escaped display data, not canonical event or identity inputs.

## Architecture and limits

Python owns source validation, matching and raw-generation checks. The canonical ruling store uses JSONL; canonical provider accounting uses local SQLite through the Python standard library. React inspection runs separately from those local persistence capabilities. The static viewer requires no hosted database. See ARCHITECTURE.md and BOLT-HANDOFF.md.

Optional local API source now wraps the same workspace and review-page producer, with bounded uploads and in-memory retention. Its ASGI/service controls pass; actual FastAPI, HTTP/browser integration and launch setup remain unverified. Current commands do not start an API. Its 20 MiB total encoded request limit is stricter than the CLI's 20 MiB per-file limit because base64 and JSON add overhead.

Implemented models and digest profiles are documented in docs/matching-models.md; generated schemas are in schemas/match/ and schemas/review/. VENDORED/ records exact source commits, file digests, notices and capability limits for the canonical modules. Product adapters use their unchanged codecs. The canonical evaluation library supplies frozen-corpus checks, statistics and paired group resampling; S4's real corpus adapter, group metadata and evaluation remain pending. See docs/LIMITATIONS.md for unsupported identities, partial workflow and unmeasured quality gates.

The local provisional CLI round trip is exercised, including normal/refused operations. Accuracy, blocking recall, review burden, provider usage/cost and latency are not yet measured. No completed browser/person/API round trip or quality result is claimed. The intended fuzzy/rules/Jev/LLM comparison must use frozen data and generated results.

The independent synthetic fixture surface in fixtures/independent contains DEV and demo sources with provisional labels. Private heldout data is excluded. Unknown and unlabelled pairs are not negatives. Source/count checks and unit/integration controls do not establish accuracy, human agreement or a finished review workflow.
