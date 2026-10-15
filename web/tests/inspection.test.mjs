import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { parseInspection, MAX_REPORT_BYTES } from "../src/inspection.ts";

const sha = "a".repeat(64);
function report() {
  return { stage: "candidates", judgment_performed: false, human_rulings_applied: false,
    validation: ["a", "b"].map(dataset_id => ({ dataset_id, accepted: true,
      source_sha256: sha, data_rows_seen: 1, accepted_record_count: 1,
      row_count_complete: true, issues: [] })),
    report: { profile: "product-candidates-v1", policy: { profile: "product-blocking-v1", per_a_cap: 50, global_cap: 500000 },
      n_a: 1, n_b: 1, possible_pairs: 1, union_enumerated_unique: 1,
      uncapped_union_lower_bound: 1, uncapped_union_upper_bound: 1,
      uncapped_union_exact: true, retained: 1, per_a_cap_drops: 0, global_cap_drops: 0,
      block_omissions: [], token_keys_truncated_records: 0 },
    candidate_preview: [{ refs: ["a", "b"].map(dataset_id => ({ dataset_id, record_id: "<script>alert(1)</script>", content_hash: sha })),
      pair_key: "pk1:" + sha, pair_generation: "pg1:" + sha, blockers: ["B1"], blocking_overlap: 2 }],
    preview_truncated: false };
}
const parse = data => parseInspection(new TextEncoder().encode(JSON.stringify(data)));
test("literal identities preserved as data, not normalized", () => {
  assert.equal(parse(report()).candidates[0].refs[0].record_id, "<script>alert(1)</script>");
});
test("bounded union and truncated preview remain explicit", () => {
  const data = report(); data.candidate_preview = []; data.preview_truncated = true;
  data.report.uncapped_union_exact = false;
  assert.equal(parse(data).truncated, true);
  assert.equal(parse(data).exact, false);
});
for (const [name, mutate] of [
  ["judgment flag", d => { d.judgment_performed = true; }],
  ["human flag", d => { d.human_rulings_applied = true; }],
  ["unknown report", d => { d.stage = "review_packet"; }],
  ["failed catalog", d => { d.validation[0].accepted = false; }],
  ["incomplete count", d => { d.validation[0].row_count_complete = false; }],
  ["issues", d => { d.validation[0].issues = [{}]; }],
  ["namespace collision", d => { d.validation[1].dataset_id = "a"; }],
  ["foreign namespace", d => { d.candidate_preview[0].refs[0].dataset_id = "other"; }],
  ["duplicate candidates", d => { d.candidate_preview.push(d.candidate_preview[0]); }],
  ["bad digest", d => { d.candidate_preview[0].refs[0].content_hash = "bad"; }],
  ["lone surrogate", d => { d.candidate_preview[0].refs[0].record_id = "\ud800"; }],
  ["UTF8 byte limit", d => { d.candidate_preview[0].refs[0].record_id = "é".repeat(4097); }],
  ["zero cap", d => { d.report.policy.per_a_cap = 0; }],
  ["enlarged cap", d => { d.report.policy.per_a_cap = 51; }],
  ["invalid total", d => { d.report.possible_pairs = 2; }],
  ["inconsistent cap counts", d => { d.report.global_cap_drops = 1; }],
  ["inverted bounds", d => { d.report.uncapped_union_upper_bound = 0; }],
  ["inconsistent truncation", d => { d.preview_truncated = true; }],
  ["invalid blocker", d => { d.candidate_preview[0].blockers = ["B3"]; }],
  ["duplicate blocker", d => { d.candidate_preview[0].blockers = ["B1", "B1"]; }],
]) test(`reject ${name}`, () => { const data = report(); mutate(data); assert.throws(() => parse(data)); });
test("malformed UTF8 and JSON rejected", () => {
  assert.throws(() => parseInspection(new Uint8Array([255])));
  assert.throws(() => parseInspection(new TextEncoder().encode("{")));
});
test("oversized input refused before parse", () => {
  assert.throws(() => parseInspection(new Uint8Array(MAX_REPORT_BYTES + 1)), /20 MiB/);
});
if (process.env.S4_CLI_SMOKE_REPORT) test("actual own Python CLI report interoperates", () => {
  const result = parseInspection(readFileSync(process.env.S4_CLI_SMOKE_REPORT));
  assert.equal(result.catalogs[0].count, 1);
  assert.equal(result.retained, 1);
});
