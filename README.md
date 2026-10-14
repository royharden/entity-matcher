# Entity Matcher

Compare two product catalogs, inspect match evidence and retain human decisions. The intended workflow exports reviewed matches and reusable matching/calibration tools.

Current release contains the package scaffold and strict matching models/identity helpers. Catalog import, matching, human review, provider integration and evaluation are not implemented. The frontend is a setup preview.

## Keyless setup

From the repository root, use Python3.12 with uv and Node22.12 or newer:

    uv sync --frozen
    uv run python -m entity_matcher
    uv run pytest
    uv run python scripts/generate_match_schemas.py --check
    uv run python scripts/check_public_tree.py
    npm ci
    npm run build

The Python command reports models-only status without keys or network calls. The static frontend builds to dist/. No listener or provider call is started by these commands.

## Architecture and limits

Python owns future matching and append-only rulings; React presents future review packets. The static viewer target uses no hosted database. Python execution and authoritative local persistence will remain separate from any static hosting. See ARCHITECTURE.md and BOLT-HANDOFF.md.

Implemented models and digest profiles are documented in docs/matching-models.md; generated schemas are in schemas/match/. Future shared-provider, generic ruling-store and evidence-resolver implementations require verified owner source. No source module is vendored yet.

Accuracy, blocking recall, review burden, provider usage/cost and latency are not yet measured. No results table or successful end-to-end comparison is claimed by this scaffold. The intended fuzzy/rules/Jev/LLM comparison must use frozen data and generated results.

Coding agents authored this code. Verification covers bootstrap checks and model/identity invariants, schema drift rejection and a detached model-only consumer. Accuracy, end-to-end review and provider behavior require later implementation and tests.
