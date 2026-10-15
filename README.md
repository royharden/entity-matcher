# Entity Matcher

Compare two product catalogs, inspect match evidence and retain human decisions. The intended workflow exports reviewed matches and reusable matching/calibration tools.

Current source validates CSVs, retrieves bounded candidates and exports review pages with aligned raw fields, machine signals and field-evidence metadata. A React viewer opens those pages and candidate previews. The Python core also provides raw-field evidence resolution, calibration attachment, conservative proposal policy, derived CSV views and rebuildable components using canonical evidence, journal and offline provider modules. Full review/replay/export orchestration, real fitted calibration and match-quality evaluation are in progress. Fuzzy scoring requires RapidFuzz and reports unavailable when it is absent.

## Keyless setup

From the repository root, use Python3.12 with uv and Node22.12 or newer:

    uv sync --frozen
    uv run python -m entity_matcher
    uv run pytest
    uv run python scripts/generate_match_schemas.py --check
    uv run python scripts/check_public_tree.py
    npm ci
    npm run test:web
    npm run build

Python3.12 and Node24 are the verified local tool versions. The Python command reports the current read-only stage without keys or network calls. The static frontend build target is dist/. These commands start no listener or provider call.

Inspect explicitly selected local files with:

    uv run python -m entity_matcher validate catalog-a.csv --dataset a
    uv run python -m entity_matcher candidates catalog-a.csv catalog-b.csv --max-output 100

These commands print JSON to standard output and leave source files unchanged. Validation findings contain row/field locations without raw cell values. Candidate previews carry identities/hashes and blocking metadata; they are not judged matches or human decisions. Distinct dataset namespaces are required. Preview truncation is explicit and separate from candidate generation caps. Secret and agent-only paths are refused before reading.

For the frozen synthetic demo, save a report and open it with **Choose report** in the viewer:

    uv run python -m entity_matcher candidates fixtures/independent/demo/catalog-A.csv fixtures/independent/demo/catalog-B.csv --aliases fixtures/independent/brand-aliases.csv --max-output 100 > candidate-preview.json

The viewer shows reported counts, caps, omissions and paged candidate identities. Search covers the imported preview. It records no decisions and does not check the report against current catalogs. Browser interaction and the current viewer's production build still require verification.

To include aligned raw fields, machine proposals and field-evidence metadata, export a review page:

    uv run python -m entity_matcher review-page catalog-a.csv catalog-b.csv --method rules --limit 50 > review-page.json

Open that JSON with the same file chooser. Each file holds at most100 pairs and20MiB of complete JSON. Use the reported next offset to request another page. A byte-limit failure produces an error and no partial page; reduce the limit and retry. Missing scorers and unfitted calibration keep proposals in review. File import grants no human or journal authority.

## Architecture and limits

Python owns source validation, matching and raw-generation checks. The canonical ruling store uses JSONL; canonical provider accounting uses local SQLite through the Python standard library. React inspection runs separately from those local persistence capabilities. The static viewer requires no hosted database. See ARCHITECTURE.md and BOLT-HANDOFF.md.

Optional local API source now wraps the same workspace and review-page producer, with bounded uploads and in-memory retention. Its ASGI/service controls pass; actual FastAPI, HTTP/browser integration and launch setup remain unverified. Current commands do not start an API. Its 20 MiB total encoded request limit is stricter than the CLI's 20 MiB per-file limit because base64 and JSON add overhead.

Implemented models and digest profiles are documented in docs/matching-models.md; generated schemas are in schemas/match/ and schemas/review/. VENDORED/ records exact source commits, file digests, notices and capability limits for the canonical modules. Product adapters use their unchanged codecs. The canonical evaluation library supplies frozen-corpus checks, statistics and paired group resampling; S4's real corpus adapter, group metadata and evaluation remain pending. See docs/LIMITATIONS.md for unsupported identities, partial workflow and unmeasured quality gates.

Accuracy, blocking recall, review burden, provider usage/cost and latency are not yet measured. No results table or completed review round trip is claimed. The intended fuzzy/rules/Jev/LLM comparison must use frozen data and generated results.

The independent synthetic fixture surface in fixtures/independent contains DEV and demo sources with provisional labels. Private heldout data is excluded. Unknown and unlabelled pairs are not negatives. Source/count checks and unit/integration controls do not establish accuracy, human agreement or a finished review workflow.
