// A bounded view of the existing readonly CLI preview, not a canonical review packet.
export const MAX_REPORT_BYTES = 20 * 1024 * 1024;
type ObjectMap = Record<string, unknown>;
export type RecordRef = { dataset_id: string; record_id: string; content_hash: string };
export type Candidate = {
  refs: [RecordRef, RecordRef]; pair_key: string; pair_generation: string;
  blockers: string[]; blocking_overlap: number;
};
export type Inspection = {
  catalogs: { dataset: string; count: number; sha256: string }[];
  candidates: Candidate[]; retained: number; possible: number; enumerated: number;
  lower: number; upper: number; exact: boolean; perADrops: number; globalDrops: number;
  omissions: number; tokenTruncations: number; truncated: boolean;
};
const digest = /^[0-9a-f]{64}$/;
function refuse(): never { throw new Error("Unsupported or inconsistent candidate report."); }
export function object(value: unknown): ObjectMap {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return refuse();
  return value as ObjectMap;
}
export function integer(value: unknown, maximum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > maximum) return refuse();
  return value;
}
export function text(value: unknown, maximum: number): string {
  if (typeof value !== "string" || !value || new TextEncoder().encode(value).length > maximum || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) return refuse();
  return value;
}
export function sha(value: unknown): string {
  if (typeof value !== "string" || !digest.test(value)) return refuse();
  return value;
}
export function array(value: unknown, maximum: number): unknown[] {
  if (!Array.isArray(value) || value.length > maximum) return refuse();
  return value;
}
export function readReportJSON(bytes: Uint8Array): unknown {
  if (bytes.byteLength > MAX_REPORT_BYTES) throw new Error("Choose a report smaller than 20 MiB.");
  let parsed: unknown;
  try { parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { throw new Error("Choose a valid UTF-8 JSON candidate report."); }
  return parsed;
}
export function parseInspection(bytes: Uint8Array): Inspection {
  const input = object(readReportJSON(bytes));
  if (input.stage !== "candidates" || input.judgment_performed !== false || input.human_rulings_applied !== false) return refuse();
  const catalogs = array(input.validation, 2).map(value => {
    const row = object(value);
    if (row.accepted !== true || row.row_count_complete !== true || array(row.issues, 0).length) return refuse();
    const count = integer(row.accepted_record_count, 10000);
    if (row.data_rows_seen !== count) return refuse();
    return { dataset: text(row.dataset_id, 256), count, sha256: sha(row.source_sha256) };
  });
  if (catalogs.length !== 2 || catalogs[0].dataset === catalogs[1].dataset) return refuse();
  const report = object(input.report), policy = object(report.policy);
  if (report.profile !== "product-candidates-v1" || policy.profile !== "product-blocking-v1") return refuse();
  const perACap = integer(policy.per_a_cap, 50), globalCap = integer(policy.global_cap, 500000);
  if (!perACap || !globalCap) return refuse();
  if (report.n_a !== catalogs[0].count || report.n_b !== catalogs[1].count) return refuse();
  const possible = integer(report.possible_pairs, 100000000);
  if (possible !== catalogs[0].count * catalogs[1].count) return refuse();
  const enumerated = integer(report.union_enumerated_unique, possible);
  const retained = integer(report.retained, Math.min(globalCap, possible));
  const perADrops = integer(report.per_a_cap_drops, possible);
  const globalDrops = integer(report.global_cap_drops, possible);
  const lower = integer(report.uncapped_union_lower_bound, possible);
  const upper = integer(report.uncapped_union_upper_bound, possible);
  if (retained + perADrops + globalDrops !== enumerated || lower > upper || lower < enumerated ||
      typeof report.uncapped_union_exact !== "boolean" || (report.uncapped_union_exact && lower !== upper)) return refuse();
  const identities = new Set<string>();
  const candidates = array(input.candidate_preview, 1000).map(value => {
    const row = object(value);
    const refs = array(row.refs, 2).map(value => {
      const ref = object(value);
      return { dataset_id: text(ref.dataset_id, 256), record_id: text(ref.record_id, 8192), content_hash: sha(ref.content_hash) };
    });
    if (refs.length !== 2 || new Set(refs.map(ref => ref.dataset_id)).size !== 2 ||
        refs.some(ref => !catalogs.some(c => c.dataset === ref.dataset_id))) return refuse();
    const key = text(row.pair_key, 68), generation = text(row.pair_generation, 68);
    if (!/^pk1:[0-9a-f]{64}$/.test(key) || !/^pg1:[0-9a-f]{64}$/.test(generation) || identities.has(key)) return refuse();
    identities.add(key);
    const blockers = array(row.blockers, 2).map(value => {
      if (value !== "B1" && value !== "B2") return refuse();
      return value;
    });
    const overlap = integer(row.blocking_overlap, 1000);
    if (!blockers.length || new Set(blockers).size !== blockers.length || !overlap) return refuse();
    return { refs: refs as [RecordRef, RecordRef], pair_key: key, pair_generation: generation, blockers, blocking_overlap: overlap };
  });
  if (candidates.length > retained || typeof input.preview_truncated !== "boolean" || input.preview_truncated !== (retained > candidates.length)) return refuse();
  return { catalogs, candidates, retained, possible, enumerated, lower, upper,
    exact: report.uncapped_union_exact, perADrops, globalDrops,
    omissions: array(report.block_omissions, 100000).length,
    tokenTruncations: integer(report.token_keys_truncated_records, 20000),
    truncated: input.preview_truncated };
}
