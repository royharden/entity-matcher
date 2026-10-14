# Bolt handoff — scaffold only

Target: No database. Root React/Vite setup preview builds to dist/ with relative asset paths. Python package is local and is not provisioned by the static frontend build.

Implemented inventory: root package/build configuration and an honest static setup preview. No catalog import/review/matching, migrations, server functions, auth, storage, provider call or published result exists yet. No provider secrets are consumed by the browser.

Current checks: npm ci, npm run typecheck, npm run build; uv sync --frozen, uv run pytest, uv run python scripts/check_public_tree.py, uv run python -m entity_matcher. Actual executions are recorded separately by the local build owner. A successful build is not Bolt import/deployment evidence.

Future Python matching/service/local persistence and model calls require their implemented backend, server-only configuration, ownership checks and verified execution. Hosting/provisioning/publication remains pending and is not requested by this document.

Future prompt scope is inspection/preparation of the finished inventory, not provisioning this incomplete workflow. Root static import compatibility must be tested when import is actually authorized.
