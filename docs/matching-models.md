# Matching models and identity

The current portable slice is shared/match/models.py with identity.py and the package __init__.py. It depends on Pydantic v2; use relative imports when copying these three files into another package. Tests copy exactly that closure into a temporary portable_match package and run an isolated consumer. No parser, normalizer, matcher, provider, evidence resolver, calibration estimator or ruling reducer is implemented.

RawProduct requires exactly eight decoded strings: id, name, brand, size, unit, price, category, description. id/name must be nonempty; optional data stays an empty string. Every string is bounded to 8192 UTF8 bytes. Namespace dataset_id is bounded to 256 UTF8 bytes. Whitespace, decimal text, case and Unicode sequences stay exact. Schema string limits count characters and carry byte-limit metadata; Python validation enforces UTF8 byte bounds. A later importer will enforce file/row limits and decoding.

RecordIdentity has dataset_id and record_id only. RecordRef adds a full lowercase SHA256 content_hash. stable_identity explicitly projects its two identity fields. PairIdentity canonically orients the two distinct identities and recomputes its full pk1 stable key and pg1 generation from aligned fingerprints. RecordRef instances or content-bearing dictionaries cannot enter pair refs without projection. Raw mutations change generation and preserve the stable pair key.

Preimages use sorted-key compact UTF8 JSON, ensure_ascii=false, no BOM/trailing LF, and no raw normalization:

- product-raw-fields-v1 hashes {algorithm,fields}, where fields contains all eight raw strings; raw generation is rg1:<digest>.
- pk1 hashes {algorithm_version:1,kind:"pair",refs:[low,high]}, sorting exact identity JSON bytes.
- pg1 hashes {algorithm_version:1,pair_key,raw_fingerprints:[low,high]}, preserving fingerprint alignment.
- product-record-field-evidence-v1 hashes {algorithm,dataset_id,record_id,source_sha256,source_revision,field_id,raw_value}. The excerpt hashes the full exact field UTF8 bytes. Blank fields produce no substantive field artifact.

FieldEvidenceIdentity is producer digest metadata. Construction from raw bytes computes these hashes; deserializing metadata can only validate shape and raw-generation agreement. A later resolver must retain and verify exact source/artifact bytes. This model is not a generic Pointer or proof that a quote exists.

JudgmentProposal keeps raw relatedness/native confidence/distribution separate from optional p_same. Native distributions must sum to one within 1e-6; they are never normalized or converted into correctness probabilities here. p_same requires explicit dev-manifest/config hashes and a positive strict support count. Those fields describe provenance and do not prove that an estimator was fitted correctly or met an accuracy target. Unsupported calibration or a named failure requires a review proposal. These frozen machine proposals have no human event/action/verdict fields and cannot be appended as generic rulings.

Use model_validate/model_validate_json for incoming values, including any copied instances. Models forbid extra fields, coercion, nonfinite numbers and invalid nested instances. Pydantic model_construct/model_copy(update=...) are trusted low-level APIs; never treat their unchecked outputs as accepted input without validation. Frozen models and tuple collections prevent ordinary mutation after acceptance.

Generate schemas with uv run python scripts/generate_match_schemas.py. The --check mode and CI reject missing, extra, changed or malformed schema files. The eight generated schemas describe wire shapes; Python validators additionally enforce cross-field digest, probability and generation relationships. Schema validation alone does not enforce those relationships or grant human authority.
