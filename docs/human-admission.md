# Trusted-host admission adapter

The product has no built-in authenticated-person issuer. The CLI and browser/file
workflow remain provisional. A launch token, typed name, imported flag or automated
test does not establish a person or their approval.

An embedding trusted host may explicitly pass a `HumanAdmissionIssuer` to
`pipeline.apply_draft.apply_draft(human_admission=...)`. Its `admit_intent` receives
the packet ID and complete provisional canonical intent. The host must authenticate
the reviewer, establish their approval of that exact operation, retain independently
verifiable evidence and return a canonical `RulingIntent`. Only `actor_label`,
`actor_provenance` and `actor_provenance_ref` may change. The actor label identifies
the authenticated subject; provenance must be `human` and the reference must identify
the retained evidence. Packet context, operation ID, action, reason, timestamp,
source generation, namespace and observed head remain exact.

The same host's `validate_human` must accept the complete final intent only when its
retained evidence is valid for that subject and operation. Success returns exactly
`None`; missing, forged, changed or revoked evidence must raise. The controller
checks the returned wire and verifier before any append, and checks evidence again
inside the journal's current-generation validation under its append lock. Source
and head checks remain mandatory. Batch writes retain their existing partial/uncertain
effect semantics; admission is not batch atomicity. Exact retries require stable
attribution/evidence and remain historical acknowledgements, never fresh source authority.

For replay and CSV export, pass that same trusted verifier as `human_evidence` to
`domain.components.project_components`, then format the resulting projection with
the existing CSV functions. Sequence assignment is excluded from canonical intent
bytes, so the verifier can check journal events against the same retained approval.
Changed or missing sources, unsure/retracted heads, absent/invalid evidence and
provisional attribution cannot create approved edges. Negative edges can mark a
component contested. Provisional new writes cannot replace a human-attributed head.

The host must supply its own actual authentication, evidence retention, verification
and revocation contract. This adapter does not provide an identity provider, evidence
store, signing key, HTTP endpoint or production issuer. Merely injecting a permissive
callback is not person authentication. Integration tests use a clearly named synthetic
host that approves only preselected test intents; their passing results are adapter
controls, not genuine-person evidence or whole-app acceptance.
