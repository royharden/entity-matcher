# Entity Matcher

Compare two product catalogs, inspect match evidence and retain human decisions. The intended workflow exports reviewed matches and reusable matching/calibration tools.

Current source contains strict matching models/identity, bounded CSV validation, separate normalization/quantity/aliases, capped candidate retrieval and exact raw-field evidence checks. Offline rule constraints are implemented; fuzzy scoring requires RapidFuzz and fails explicitly if absent. A complete comparison workflow, fitted calibration, human review, provider integration and accuracy evaluation are not implemented. The frontend is a setup preview.

## Keyless setup

From the repository root, use Python3.12 with uv and Node22.12 or newer:

    uv sync --frozen
    uv run python -m entity_matcher
    uv run pytest
    uv run python scripts/generate_match_schemas.py --check
    uv run python scripts/check_public_tree.py
    npm ci
    npm run build

The Python command reports the current read-only stage without keys or network calls. The static frontend builds to dist/. No listener or provider call is started by these commands.

Inspect explicitly selected local files with:

    uv run python -m entity_matcher validate catalog-a.csv --dataset a
    uv run python -m entity_matcher candidates catalog-a.csv catalog-b.csv --max-output 100

These commands print JSON to standard output and leave source files unchanged. Validation findings contain row/field locations without raw cell values. Candidate previews carry identities/hashes and blocking metadata; they are not judged matches or human decisions. Distinct dataset namespaces are required. Preview truncation is explicit and separate from candidate generation caps. Secret and agent-only paths are refused before reading.

## Architecture and limits

Python owns future matching and append-only rulings; React presents future review packets. The static viewer target uses no hosted database. Python execution and authoritative local persistence will remain separate from any static hosting. See ARCHITECTURE.md and BOLT-HANDOFF.md.

Implemented models and digest profiles are documented in docs/matching-models.md; generated schemas are in schemas/match/. Generic provider, ruling-store and evidence-dispatch implementations require verified owner source. No foreign module is vendored yet. See docs/LIMITATIONS.md for the partial workflow and unmeasured quality gates.

Accuracy, blocking recall, review burden, provider usage/cost and latency are not yet measured. No results table or successful end-to-end comparison is claimed by this scaffold. The intended fuzzy/rules/Jev/LLM comparison must use frozen data and generated results.

Coding agents authored this code. Verification covers bootstrap checks and model/identity invariants, schema drift rejection and a detached model-only consumer. Accuracy, end-to-end review and provider behavior require later implementation and tests.
