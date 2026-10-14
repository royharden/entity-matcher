# Current limitations

This is partial source, with read-only validate/candidates CLI commands and a static setup preview. It does not yet perform the full judge, calibrate, review, journal-replay and export workflow. The implemented CSV validator accepts only the fixed eight-column schema, bounded strict decoding and explicit supported legacy overrides. Raw IDs/strings remain exact; normalization is separate. CLI inspection leaves source files unchanged and performs no model dispatch or human-ruling operation.

Quantity parsing supports declared code units, complete decimal expressions and optional integer pack prefixes. Bare ounces are ambiguous; US fluid ounces are an explicit default that can be rejected. Unknown quantity/brand data is not equality. Price is nonnegative Decimal metadata. Neither raw relatedness nor native confidence is a correctness probability.

Candidate retrieval uses two bounded inverted indexes. High-frequency tokens are pruned and oversized postings are refined/capped; these omissions can lose matches. Per-A/global caps and deterministic drop digests are disclosed. Original unenumerated union counts remain conservative bounds. Computational stress controls are not an accuracy benchmark and prove no blocking recall or match precision.

Rule conflicts and exact product-field evidence checks are implemented. A missing RapidFuzz scorer produces named unavailable, with no replacement implementation. Calibrated probabilities require actual fitted development evidence; none has been fitted. Unsupported calibration requires review. Machine proposals have no human-ruling authority.

Generic provider/call-ledger, human-ruling store, evaluation framework, evidence dispatcher and input-screen integration are pending verified owner source. Live Jev/OpenAI access, current pricing, cumulative budget authority and bounded test dispatch remain separate execution gates. No credentials are consumed by this source stage.

Required independent synthetic fixtures and frozen development/heldout splits, public non-model cohort, accuracy floors, user-flow tests, API/file review round trips, reusable final exports and independent finished-app review remain pending. Missing features and measurements are not waived by successful source tests or package builds. Bolt import/deployment has not been performed.
