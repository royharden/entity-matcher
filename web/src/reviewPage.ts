// Structural file-view boundary. Cryptographic preimages/current-source checks stay in Python.
import schema from "../../schemas/review/product-review-page.schema.json" with { type: "json" };
import { array, integer, object, readReportJSON, sha, text, type RecordRef } from "./inspection.ts";

export const RAW_FIELDS = ["id", "name", "brand", "size", "unit", "price", "category", "description"] as const;
type RawField = typeof RAW_FIELDS[number];
type RawRecord = Record<RawField, string>;
type SchemaShape = { properties: Record<string, unknown> };
const definitions = schema.$defs as unknown as Record<string, SchemaShape>;
function fail(): never { throw new Error("Unsupported or inconsistent review page."); }
function closed(value: unknown, name?: string): Record<string, unknown> {
  const row = object(value), shape = name ? definitions[name] : schema;
  if (!shape || Object.keys(row).some(key => !Object.hasOwn(shape.properties, key))) return fail();
  return row;
}
function bool(value: unknown): boolean { if (typeof value !== "boolean") return fail(); return value; }
function version(row: Record<string, unknown>) { if (row.schema_version !== 1) return fail(); }
function nullableText(value: unknown, max = 96): string | null { return value === null ? null : text(value, max); }
function code(value: unknown): string {
  const result = text(value, 96); if (!/^[a-z][a-z0-9_.-]*$/.test(result)) return fail(); return result;
}
function probability(value: unknown): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) return fail();
  return value;
}
function recordRef(value: unknown): RecordRef {
  const row = closed(value, "RecordRef");
  return { dataset_id: text(row.dataset_id, 256), record_id: text(row.record_id, 8192), content_hash: sha(row.content_hash) };
}
function sameRef(a: RecordRef, b: RecordRef) { return a.dataset_id === b.dataset_id && a.record_id === b.record_id && a.content_hash === b.content_hash; }
function pairIdentity(value: unknown) {
  const row = closed(value, "PairIdentity"); version(row);
  const refs = array(row.refs, 2).map(value => {
    const ref = closed(value, "RecordIdentity");
    return { dataset_id: text(ref.dataset_id, 256), record_id: text(ref.record_id, 8192) };
  });
  const fingerprints = array(row.raw_fingerprints, 2).map(sha);
  const key = text(row.pair_key, 68), generation = text(row.pair_generation, 68);
  if (refs.length !== 2 || fingerprints.length !== 2 || refs[0].dataset_id === refs[1].dataset_id ||
      !/^pk1:[0-9a-f]{64}$/.test(key) || !/^pg1:[0-9a-f]{64}$/.test(generation)) return fail();
  return { key, generation, refs: refs.map((ref, i) => ({ ...ref, content_hash: fingerprints[i] })) };
}
function samePair(a: ReturnType<typeof pairIdentity>, b: ReturnType<typeof pairIdentity>) {
  return a.key === b.key && a.generation === b.generation && a.refs.every((ref, i) => sameRef(ref, b.refs[i]));
}
export type RawView = { ref: RecordRef; raw: RawRecord };
export type EvidenceView = { record: RecordRef; field: RawField; status: string; quote: string | null; issue: string | null };
export type ReviewItemView = {
  key: string; generation: string; records: RawView[]; evidence: EvidenceView[];
  method: string; status: string; outcome: string; relatedness: number | null;
  nativeConfidence: number | null; pSame: number | null; issues: string[];
};
export type ReviewPage = {
  workspace: string; catalogs: { dataset: string; count: number; sha256: string }[];
  retained: number; possible: number; lower: number; upper: number; exact: boolean;
  perADrops: number; globalDrops: number; omissions: number; tokenTruncations: number;
  items: ReviewItemView[]; offset: number; limit: number; nextOffset: number | null;
  truncated: boolean; unsupported: number;
};
function rawRecord(value: unknown): RawRecord {
  const row = closed(value, "RawProduct"), result = {} as RawRecord;
  if (Object.keys(row).length !== RAW_FIELDS.length) return fail();
  for (const field of RAW_FIELDS) result[field] = row[field] === "" ? "" : text(row[field], 8192);
  if (!result.id || !result.name) return fail();
  return result;
}
function artifactRef(value: unknown) {
  if (value === null) return;
  const row = closed(value, "ArtifactRef");
  text(row.artifact_id, 256); text(row.profile_id, 256); sha(row.artifact_sha256);
}
function evidence(value: unknown, records: RawView[]): EvidenceView {
  const row = closed(value, "FieldCheck"), ref = recordRef(row.record);
  const field = text(row.field_id, 20) as RawField;
  const source = records.find(item => sameRef(item.ref, ref));
  if (!source || !RAW_FIELDS.includes(field) || !source.raw[field]) return fail();
  const issue = row.issue_code === null ? null : code(row.issue_code);
  if (row.pointer === null) {
    if (row.resolution !== null || issue !== "canonical_identity_unsupported") return fail();
    return { record: ref, field, status: "unsupported", quote: null, issue };
  }
  const pointer = closed(row.pointer, "ProductPointer"); version(pointer);
  if (pointer.kind !== "row:id" || pointer.source_type !== "product_catalog" || pointer.locator_subtype !== "record_field") return fail();
  const locator = closed(pointer.locator, "ProductLocator");
  const artifact = sha(pointer.evidence_artifact_sha256), excerpt = sha(locator.excerpt_sha256);
  artifactRef(pointer.provenance_ref);
  if (pointer.source_id !== ref.dataset_id || sha(pointer.source_sha256) !== ref.content_hash ||
      pointer.source_revision !== "rg1:" + ref.content_hash || locator.dataset_id !== ref.dataset_id ||
      locator.record_id !== ref.record_id || locator.field_id !== field || locator.quote !== source.raw[field]) return fail();
  const resolution = closed(row.resolution, "Resolution");
  const status = text(resolution.status, 20);
  if (!["resolved", "invalid", "stale", "missing", "unsupported"].includes(status)) return fail();
  code(resolution.reason);
  if (resolution.provenance !== null) closed(resolution.provenance, "ResolutionProvenance");
  if (status === "resolved") {
    if (issue !== null || resolution.scope !== "text" || resolution.excerpt !== locator.quote ||
        resolution.checked_source_revision !== pointer.source_revision || resolution.checked_source_sha256 !== ref.content_hash ||
        resolution.checked_artifact_sha256 !== artifact || resolution.excerpt_sha256 !== excerpt) return fail();
  } else if (resolution.scope !== null || resolution.excerpt !== null || resolution.excerpt_sha256 !== null || issue !== resolution.reason) return fail();
  return { record: ref, field, status, quote: status === "resolved" ? source.raw[field] : null, issue };
}
function reviewItem(value: unknown): ReviewItemView {
  const row = closed(value, "ReviewItem"), pair = pairIdentity(row.pair);
  const records = array(row.records, 2).map(value => {
    const item = closed(value, "ReferencedProduct"), ref = recordRef(item.ref), raw = rawRecord(item.raw);
    if (ref.record_id !== raw.id) return fail();
    return { ref, raw };
  });
  if (records.length !== 2 || !records.every((item, i) => sameRef(item.ref, pair.refs[i]))) return fail();
  const proposal = closed(row.proposal, "ReviewProposal"); version(proposal);
  if (proposal.kind !== "review_proposal" || !samePair(pair, pairIdentity(proposal.pair))) return fail();
  const outcome = text(proposal.proposed_outcome, 20);
  if (!["merge", "review", "unlinked"].includes(outcome)) return fail();
  const judgment = closed(proposal.judgment, "JudgmentProposal"); version(judgment);
  const method = text(judgment.method, 20), status = text(judgment.status, 20);
  if (judgment.kind !== "judgment_proposal" || !["fuzzy", "rules", "jev", "llm"].includes(method) || !["ok", "unavailable", "invalid", "error"].includes(status)) return fail();
  const relatedness = probability(judgment.relatedness), nativeConfidence = probability(judgment.native_confidence), pSame = probability(judgment.p_same);
  const probabilities = judgment.native_probabilities === null ? null : array(judgment.native_probabilities, 10).map(value => {
    const p = probability(value); if (p === null) return fail(); return p;
  });
  if (probabilities && (probabilities.length < 2 || Math.abs(probabilities.reduce((a, b) => a + b, 0) - 1) > 1e-6)) return fail();
  if ((pSame === null) !== (judgment.calibration === null)) return fail();
  if (judgment.calibration !== null) {
    const calibration = closed(judgment.calibration, "CalibrationEvidence");
    sha(calibration.dev_manifest_sha256); sha(calibration.config_sha256);
    if (!integer(calibration.support_count, 100000000)) return fail();
  }
  const issues = array(judgment.issue_codes, 32).map(code);
  if (status === "ok" ? relatedness === null : (!issues.length || [relatedness, nativeConfidence, pSame, probabilities].some(value => value !== null))) return fail();
  if (pSame === null && outcome !== "review") return fail();
  const reasons = array(proposal.reason_codes, 32).map(code);
  const checks = array(row.evidence, 16).map(value => evidence(value, records));
  const expected = new Set(records.flatMap(record => RAW_FIELDS.filter(field => record.raw[field]).map(field => JSON.stringify([record.ref.dataset_id, record.ref.record_id, field]))));
  const actual = new Set(checks.map(check => JSON.stringify([check.record.dataset_id, check.record.record_id, check.field])));
  if (actual.size !== checks.length || actual.size !== expected.size || [...actual].some(key => !expected.has(key))) return fail();
  return { key: pair.key, generation: pair.generation, records, evidence: checks, method, status, outcome, relatedness, nativeConfidence, pSame, issues: [...new Set([...issues, ...reasons])] };
}
export function parseReviewPage(bytes: Uint8Array): ReviewPage {
  const page = closed(readReportJSON(bytes)); version(page);
  if (page.kind !== "product_review_page" || page.authority !== "machine_proposals_only") return fail();
  const workspace = text(page.workspace_id, 67); if (!/^s4-[0-9a-f]{64}$/.test(workspace)) return fail();
  const catalogs = array(page.catalogs, 2).map(value => {
    const row = closed(value, "CatalogStamp");
    if (row.row_count_complete !== true || !["utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1"].includes(text(row.encoding, 20))) return fail();
    return { dataset: text(row.dataset_id, 256), count: integer(row.record_count, 10000), sha256: sha(row.source_sha256) };
  });
  if (catalogs.length !== 2 || catalogs[0].dataset === catalogs[1].dataset) return fail();
  const report = closed(page.report, "CandidateOverview");
  if (report.profile !== "product-candidates-v1" || report.n_a !== catalogs[0].count || report.n_b !== catalogs[1].count) return fail();
  const possible = integer(report.possible_pairs, 100000000), retained = integer(report.retained, 500000);
  const perA = integer(report.per_a_cap, 50), global = integer(report.global_cap, 500000);
  const enumerated = integer(report.union_enumerated_unique, possible);
  const b1 = integer(report.b1_enumerated_unique, possible), b2 = integer(report.b2_enumerated_unique, possible);
  const lower = integer(report.uncapped_union_lower_bound, possible), upper = integer(report.uncapped_union_upper_bound, possible);
  const perADrops = integer(report.per_a_cap_drops, possible), globalDrops = integer(report.global_cap_drops, possible);
  const exact = bool(report.uncapped_union_exact);
  if (!perA || !global || possible !== catalogs[0].count * catalogs[1].count || retained > Math.min(global, catalogs[0].count * perA) ||
      retained + perADrops + globalDrops !== enumerated || lower !== enumerated || lower > upper || (exact && lower !== upper) ||
      Math.max(b1, b2) > enumerated || enumerated > b1 + b2) return fail();
  const offset = integer(page.offset, retained), limit = integer(page.limit, 100);
  const items = array(page.items, 100).map(reviewItem), keys = new Set(items.map(item => item.key));
  if (!limit || keys.size !== items.length || items.length !== Math.min(limit, retained - offset) ||
      items.some(item => item.records.some(record => !catalogs.some(c => c.dataset === record.ref.dataset_id)))) return fail();
  const end = offset + items.length, nextOffset = page.next_offset === null ? null : integer(page.next_offset, retained);
  const truncated = bool(page.preview_truncated), unsupported = integer(page.unsupported_records_on_page, 200);
  const unsupportedIds = new Set(items.flatMap(item => item.evidence.filter(e => e.issue === "canonical_identity_unsupported").map(e => JSON.stringify([e.record.dataset_id, e.record.record_id]))));
  if (nextOffset !== (end < retained ? end : null) || truncated !== (items.length < retained) || unsupported !== unsupportedIds.size) return fail();
  return { workspace, catalogs, retained, possible, lower, upper, exact, perADrops, globalDrops,
    omissions: integer(report.block_omission_count, 100000000), tokenTruncations: integer(report.token_keys_truncated_records, 20000),
    items, offset, limit, nextOffset, truncated, unsupported };
}
