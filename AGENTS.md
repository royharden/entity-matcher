# Product instructions v1

This inner repository contains the product only. Agent coordination, registry, plans, receipts and chat exports belong in the parent shell and are not runtime dependencies. Keep instruction files limited to product commands and stable invariants. Canonical local skills and operating grants are referenced by the parent shell; do not copy them into this public product repository.

Current scope is package/configuration scaffold and schema proposals. Matching, provider integration, shared exports and interactive review are not implemented.

Use Python3.12 and uv from the repository root: uv sync --frozen; uv run ruff check .; uv run ruff format --check .; uv run pytest; uv run python scripts/check_public_tree.py; uv run python -m entity_matcher. Frontend: npm ci; npm run typecheck; npm run build. No command here starts a listener.

Fixtures/gold must be synthetic or verified public data; never commit real customer data, credentials, runtime journals, uploaded inputs, caches, local databases or agent material. Provider secrets stay server-side; no VITE_ secret variables. No automatic source-record changes or model-labelled human rulings. Stable pair identity uses dataset ID and opaque record ID only; raw content generations are separate.

Owner contracts must be adopted before shared implementation or copying. Proposed schema examples are not executable contracts. Never invent source commits or measured model results. Any listener, provider call, child or heavy operation requires its actual resource/grant scope.

Changelog: v1 establishes root commands, product-only layout and the scaffold boundary.
