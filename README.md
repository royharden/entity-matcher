# Entity Matcher

See [the current checkpoint](DEVELOPMENT-STATUS.md) for implemented behavior, verification gaps, outstanding tasks and lessons.

Compare two product catalogs and record provisional review decisions locally. CSV inputs remain unchanged; decisions use a separate JSONL journal. Genuine human admission and measured match quality remain unfinished.

The CLI validates catalogs, retrieves bounded candidates, exports raw-field review packets, applies provisional drafts, replays decisions and exports display CSVs. The React viewer supports packet inspection, decision drafting, file import/download and an optional local CSV comparison preview. Its local production build passes. Headless Chromium verifies bounded file/draft/download cases and the local API preview, token/CSV refusals and draft replacement consent. OS file-picker/save dialogs and the complete person-review workflow remain unverified. Fuzzy/rules scoring requires RapidFuzz and reports unavailable when absent; unfitted probabilities stay unavailable and proposals stay in review.

## Keyless setup

From the repository root, use Python 3.12 with uv. The smallest runnable workflow needs no frontend or provider key:

    uv sync --frozen
    uv run python scripts/demo.py --out .local/demo-first

Use a new output directory for each run; existing directories refuse without overwriting. The demo authors two tiny synthetic catalogs, calls the actual CLI, appends an explicitly automated provisional decision, retries the same operation, retracts it and exports CSVs. Retrying the old decision after retraction leaves the tombstone effective. Edited source and malformed CSV both refuse an apply with unchanged journal bytes; malformed validation also reports its finding. RESULT.json records actual exits, input preservation, signal availability and artifact hashes. This is a workflow demonstration, not a person review or accuracy evaluation.

Optional local comparison dependencies are declared as `api` and `fuzzy` extras. Install them with `uv sync --frozen --extra api --extra fuzzy`, and retain those selections when running, for example `uv run --frozen --extra api --extra fuzzy python -m entity_matcher serve --help`. The baseline install keeps these optional. The `build` dependency group pins the wheel backend for offline builds after setup.

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

Python 3.12 and Node 24 are the local tool versions. The no-argument Python command reports actual provisional capabilities, with human admission disabled. Node transport tests include real Python decoder/journal interoperability and therefore need the Python environment. The frontend build target is dist/; local typecheck, 128 web controls and Vite production compilation pass in a fresh source export. Browser verification covers the bounded file and local API cases described here. These commands start no application listener or provider call.

Inspect explicitly selected local files with:

    uv run python -m entity_matcher validate catalog-a.csv --dataset a
    uv run python -m entity_matcher candidates catalog-a.csv catalog-b.csv --max-output 100

These commands print JSON to standard output and leave source files unchanged. Validation findings contain row/field locations without raw cell values. Candidate previews carry identities/hashes and blocking metadata; they are not judged matches or human decisions. Distinct dataset namespaces are required. Preview truncation is explicit and separate from candidate generation caps. Secret and agent-only paths are refused before reading.

For the frozen synthetic demo, save a report and open it with **Choose report** in the viewer:

    uv run python -m entity_matcher candidates fixtures/independent/demo/catalog-A.csv fixtures/independent/demo/catalog-B.csv --aliases fixtures/independent/brand-aliases.csv --max-output 100 > candidate-preview.json

The viewer shows reported counts, caps, omissions and paged candidate identities. Search covers the imported preview. Candidate reports and review pages are inspection-only; ruling packets enable provisional local drafts. File import does not establish source freshness or authority. The initial file-inspection/draft/download path is exercised; complete browser interaction still requires verification.

To include aligned raw fields, machine proposals and field-evidence metadata, export a review page:

    uv run python -m entity_matcher review-page catalog-a.csv catalog-b.csv --method rules --limit 50 > review-page.json

Open that JSON with the same file chooser. Each file holds at most100 pairs and20MiB of complete JSON. Use the reported next offset to request another page. A byte-limit failure produces an error and no partial page; reduce the limit and retry. Missing scorers and unfitted calibration keep proposals in review. File import grants no human or journal authority.

## Local CSV preview

Build the viewer with `npm run build`, then serve `dist` from HTTP loopback, for example `python -m http.server 8080 --bind 127.0.0.1 --directory dist`. In another terminal, configure a fresh `ENTITY_MATCHER_REVIEW_TOKEN` in the server environment and start `uv run --frozen --extra api --extra fuzzy python -m entity_matcher serve --port 8081 --frontend-port 8080`. Use available ports and match the frontend origin exactly. The token is a runtime connection value; never put it in build variables, command arguments or source files.

Open the viewer at `http://127.0.0.1:8080`, choose **Connect local service**, enter port 8081 and the same review token, then select two CSV catalogs. **Compare catalogs** sends their exact original bytes to that local service and opens its first page of at most 50 candidate pairs. The client checks the existing page schema and both source SHA-256 values. It does not fetch later API pages; use the CLI review-page command for other offsets. The combined encoded upload, including base64 and JSON overhead, must fit within 20 MiB. A 15-second timer aborts pending network requests. If it fires during source hashing, the client refuses the result when hashing settles; it cannot interrupt hashing or guarantee an end-to-end deadline.

The preview is read-only; file ruling packets still supply provisional decision drafts. A valid API response asks before replacing an existing draft. Keep restores its focus/selection and preserves exact downloaded draft bytes. Errors and superseded requests preserve the current report/draft. Closing the local panel clears connection fields, and new inputs or draft edits cancel the pending client request. Connection details are not saved to browser storage. Hosted, HTTPS and file-origin viewers use exported reports; this local connection requires HTTP localhost.

The production browser/API check exercises normal CSV upload, a wrong-token refusal, malformed CSV refusal, exact saved-draft preservation on Keep and explicit replacement. The browser hides the guard's token403 behind a CORS network error; the viewer gives port/token/origin guidance without displaying remote error text. CSV validation returns422. The test browser acknowledged close but needed forced cleanup after its exit deadline; graceful Chromium process shutdown remains unverified.

## File review commands

For your own catalogs, create an owner-controlled journal directory first. Use the same explicit namespace and original packet when applying its downloaded decisions:

    uv run python -m entity_matcher packet catalog-a.csv catalog-b.csv --journal-dir local-journal --namespace my-products > review-packet.json
    uv run python -m entity_matcher apply-rulings catalog-a.csv catalog-b.csv --journal-dir local-journal --namespace my-products --packet review-packet.json --draft entity-matcher-decisions.json
    uv run python -m entity_matcher export catalog-a.csv catalog-b.csv --journal-dir local-journal --namespace my-products --view pairs > pair-review.csv

When the frontend is running, open review-packet.json with Choose report. Match/different/unsure choices and reasons stay in local draft state. Download decisions and apply them with Python; the browser cannot write the journal. A replacement file is validated before asking to discard an existing draft. The in-page dialog initially focuses Keep current draft; Cancel, Escape and default Enter preserve the draft. Explicit Replace accepts the validated file. Editing a draft invalidates a pending read. Focus and text selection return on cancellation. Real Chromium report-replacement checks cover initial focus, Tab cycling, blocked background focus, Escape/default Enter, Keep, malformed replacement and explicit Replace. A separate real Chromium decisions-file check preserves identical downloaded draft bytes through Escape, default Enter, Keep and malformed input; explicit Replace restores the exact original saved draft, which Python applies and retries without changing journal bytes. Skip/clearing an unsubmitted draft creates no event. Generate a fresh packet before retracting a recorded decision. Existing-head retraction appends a tombstone rather than reviving earlier decisions.

Apply exits 0 for complete acknowledgement, 2 for invalid/stale inputs, and 3 for a stopped or uncertain write. Receipts disclose acknowledged effects and remaining operation IDs; batches do not roll back. Preserve an uncertain draft and retry its exact operation IDs after reconciliation. A historical retry receipt grants no new current authority. Damaged journals refuse new writes and CSV export; no automatic tail repair is supplied.

Packets/drafts contain at most 100 pairs/operations and 20 MiB of JSON. Current source/head checks run in Python. Neither imported claims nor a browser token/name/flag admits a human; that branch is disabled. CSV exports are escaped display data, not canonical event or identity inputs.

## Architecture and limits

Python owns source validation, matching and raw-generation checks. The canonical ruling store uses JSONL; canonical provider accounting uses local SQLite through the Python standard library. React inspection runs separately from those local persistence capabilities. The static viewer requires no hosted database. See ARCHITECTURE.md and BOLT-HANDOFF.md.

The optional local API wraps the same workspace and review-page producer, with bounded uploads and in-memory retention. Its explicit `serve` command binds only `127.0.0.1`, with one worker, no proxy trust and no access logs. Real loopback HTTP checks exercise upload/page parity, token/Origin/Host refusals, malformed input, declared body limits and busy rejection during an incomplete upload. Request-count shutdown and actual listener closure are exercised. An idle Windows Ctrl+C check passes against the real launcher in an isolated hidden console: the status route answers before the signal, the server launcher and interpreter exit normally, the CLI reports stopped and the listener closes. Bounded API-to-browser preview/refusal/draft-consent cases pass. Shutdown during active requests and other signal/platform paths remain unverified. The lock now includes optional API and RapidFuzz dependencies. A fresh Windows Python 3.12 environment passed hash-checked dependency installation, offline frozen-lock reconciliation and a separately built product-wheel install; a direct frozen registry sync into an absent environment and cache also passed with audited build constraints. Other operating systems remain unverified. Its 20 MiB total encoded request limit is stricter than the CLI's 20 MiB per-file limit because base64 and JSON add overhead.

Inspect the launch options with:

    uv run python -m entity_matcher serve --help

Supply an explicit free port with `--port` and a fresh, server-only `ENTITY_MATCHER_REVIEW_TOKEN` environment value of 32â€“128 letters, digits, underscores or hyphens. The example environment file is not automatically loaded. Missing or invalid configuration refuses before starting a listener; missing optional packages produce a named unavailable result. Never put the token in browser build variables or command arguments. It permits bounded in-memory comparison uploads and creates no verified Human or journal authority.

`--frontend-port` admits at most two additional loopback frontend origins. `--max-requests` optionally stops the server after 1â€“10,000 HTTP requests, including refusals. The initial `starting` JSON is a launch notice; `/api/status` establishes actual readiness. Workspaces are memory-only and disappear at shutdown. The optional local CSV form uses this API; exported-file review remains independently usable.

Implemented models and digest profiles are documented in docs/matching-models.md; generated schemas are in schemas/match/ and schemas/review/. VENDORED/ records exact source commits, file digests, notices and capability limits for the canonical modules. Product adapters use their unchanged codecs. The canonical evaluation library supplies frozen-corpus checks, statistics and paired group resampling; S4's real corpus adapter, group metadata and evaluation remain pending. See docs/LIMITATIONS.md for unsupported identities, partial workflow and unmeasured quality gates.

The local provisional CLI round trip is exercised, including normal/refused operations. Accuracy, blocking recall, review burden, provider usage/cost and latency are not yet measured. A complete person-review workflow and remaining browser/API lifecycle cases remain unverified; no quality result is claimed. The intended fuzzy/rules/Jev/LLM comparison must use frozen data and generated results.

The independent synthetic fixture surface in fixtures/independent contains DEV and demo sources with provisional labels. Private heldout data is excluded. Unknown and unlabelled pairs are not negatives. Source/count checks and unit/integration controls do not establish accuracy, human agreement or a finished review workflow.

Windows frontend relocation is also exercised: a clean tracked-source export installs 63 packages from the retained locked npm cache with lifecycle scripts disabled, then passes all 106 web controls, typecheck and the production build. The three static output files reproduce the prior build byte for byte. The complete web suite includes a real Python journal interoperability check, so its fresh export also needs the documented Python environment; Node-only installation is not a complete 106-test setup. This is local static-build portability, not actual browser behavior, other-platform verification or Bolt import.
