# Models-only boundaries

src/entity_matcher is an installed Python package with a keyless setup-state command. shared/match/{models,identity}.py defines strict immutable models and versioned identity preimages using Pydantic v2 and the standard library. It has no matcher, journal, service or provider client. web/src is a static React setup preview with root Vite configuration. scripts/check_public_tree.py is a repository guard, not an input-screening or matching library.

Future match logic belongs in a dependency-light canonical match module; product I/O and review orchestration remain separate. Generic rulings/evalkit, providers/callledger and evidence dispatch remain owned upstream and require adopted contracts and verified source commits. Stable identities exclude content fingerprints; generation evidence binds raw content separately.

The product repository contains no agent plans/registries/transcripts, shared workspace imports or runtime data. Dependency locks describe the model/scaffold/testing/frontend tools used now. Future-only OCR, model SDKs, matcher libraries and services are deferred. Model schema validation does not certify calibration, evidence resolution, match correctness or a human decision.
