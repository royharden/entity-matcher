# Entity Matcher

Compare two product catalogs, inspect match evidence and retain human decisions. The intended workflow exports reviewed matches and reusable matching/calibration tools.

Current source validates CSVs, retrieves bounded candidates and opens candidate reports in a local React viewer. The Python core also provides raw-field evidence resolution, calibration attachment, conservative proposal policy and rebuildable components using the canonical evidence, journal and offline provider modules. Full comparison/review/export orchestration, real fitted calibration and match-quality evaluation are in progress. Fuzzy scoring requires RapidFuzz and reports unavailable when it is absent.

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

## Architecture and limits

Python owns source validation, matching and raw-generation checks. The canonical ruling store uses JSONL; canonical provider accounting uses local SQLite through the Python standard library. React inspection runs separately from those local persistence capabilities. The static viewer requires no hosted database. See ARCHITECTURE.md and BOLT-HANDOFF.md.

Implemented models and digest profiles are documented in docs/matching-models.md; generated schemas are in schemas/match/. VENDORED/ records exact source commits, file digests, notices and capability limits for the installed canonical modules. Product adapters use their unchanged codecs. See docs/LIMITATIONS.md for unsupported identities, partial workflow and unmeasured quality gates.

Accuracy, blocking recall, review burden, provider usage/cost and latency are not yet measured. No results table or successful end-to-end comparison is claimed by this scaffold. The intended fuzzy/rules/Jev/LLM comparison must use frozen data and generated results.

The independent synthetic fixture surface in fixtures/independent contains DEV and demo sources with provisional labels. Private heldout data is excluded. Unknown and unlabelled pairs are not negatives. Source/count checks and unit/integration controls do not establish accuracy, human agreement or a finished review workflow.
