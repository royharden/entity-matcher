# Boundary proposals — specification only

These examples do not implement or adopt shared schemas.

Product CSV proposal: exactly id,name,brand,size,unit,price,category,description; 10,000 rows/file,20MiB/file,8KiB/cell. IDs preserve exact opaque decoded strings. Text normalization and numeric quantity parsing must not mutate raw identity. UTF/BOM detection with strict decoding; ambiguous legacy encoding requires explicit override.

Stable pair proposal: canonical sorted identity refs, each containing dataset_id and opaque record_id only. A RecordRef may also carry content_hash, but that field is projected OUT of stable-key bytes. Separate generation binds aligned raw-record fingerprints. Raw changes invalidate human authority; parser/alias changes affect scoring context rather than raw generations. Tombstone of the effective decision returns unresolved review, without resurrecting older approvals. Exact envelope/action/CAS/serialization is an owner-coordinated adoption gate.

Judge proposal: fuzzy/rules/Jev/LLM behind a common interface, raw relatedness/native distributions separate from optional supported calibrated probability. Three-level Jev Score plus three Nouls in one request. Unknown billed usage retains liability; an unbilled output-null can coexist with known input-only priced cost. Sequential bounded provider calls only after actual adopted owner source and testing grant; no adapter exists in this scaffold.

Review packet proposal: immutable source/generation evidence, proposed outcome, explicit human verdict, stale/orphan/contested status. Skip means defer/no event. File import and API use the same future reducer. Only current human positives form authoritative clusters; negative conflicts block approved export. This document supplies no executable model or validator for those contested contracts.
