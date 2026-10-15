# Bolt handoff — partial source

Target: static local-report inspection without a hosted database. Root React/Vite uses relative asset paths and targets dist/. The current viewer source passes TypeScript/parser controls; its production build/browser flow remains to be verified. Python execution is separate from the frontend build.

Implemented source: bounded CSV/candidate core, strict models/identity, offline rules, calibration attachment/policy, exact retained product-field resolution, canonical JSONL ruling store and product freshness/component projection, canonical offline provider controller with local SQLite accounting, and frozen synthetic DEV/demo fixtures. Fuzzy requires a separately installed RapidFuzz dependency. Complete review orchestration, fitted calibration, running API, human verification, live model adapters and quality results remain pending. The browser consumes no provider secrets.

Current checks: npm ci, npm run typecheck, npm run build; uv sync --frozen, uv run pytest, uv run python scripts/generate_match_schemas.py --check, uv run python scripts/check_public_tree.py, uv run python -m entity_matcher. Actual executions are recorded separately by the local build owner. A successful build is not Bolt import/deployment evidence.

Python services, JSONL rulings, SQLite accounting and future model calls need an implemented backend with server-only configuration and verified lifecycle. Static frontend hosting does not execute Python or provision those capabilities. Hosting/provisioning/publication remains pending.

Preparation prompt: inspect this repository and preserve the root Vite frontend and separate Python boundary. Keep file inspection usable without hosted services. Report the unimplemented backend and verification gates before proposing provisioning. Request no provider keys in browser code. Test actual static import compatibility when import is authorized; a local build alone is not deployment evidence.
