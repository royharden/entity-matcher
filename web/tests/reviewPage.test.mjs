import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { parseReviewPage } from "../src/reviewPage.ts";

const fixture = readFileSync(new URL("./fixtures/review-page.json", import.meta.url));
const source = () => JSON.parse(fixture.toString("utf-8"));
const parse = data => parseReviewPage(new TextEncoder().encode(JSON.stringify(data)));

test("actual Python producer interoperates with bounded readonly UI view", () => {
  const page = parseReviewPage(fixture);
  assert.equal(page.items.length, 1);
  assert.equal(page.retained, 1);
  assert.equal(page.items[0].records[0].raw.name, "Café Tea");
  assert.equal(page.items[0].records[0].raw.description, "<script>literal text</script>");
  assert.equal(page.items[0].evidence.length, 16);
  assert.equal(page.items[0].outcome, "review");
  assert.equal(page.items[0].pSame, null);
  assert.equal("journal_write_authority" in page, false);
});
for (const [name, mutate] of [
  ["human authority", p => { p.authority = "human_approved"; }],
  ["actor", p => { p.actor_label = "human"; }],
  ["event", p => { p.event_id = "pretend"; }],
  ["sequence", p => { p.sequence = 1; }],
  ["prototype-key extra", p => { p.__proto__ = null; Object.defineProperty(p, "__proto__", {value:{},enumerable:true}); }],
  ["unknown nested field", p => { p.items[0].records[0].raw.execute = "instruction"; }],
  ["oversized raw bytes", p => { p.items[0].records[0].raw.description = "é".repeat(4097); }],
  ["unpaired surrogate", p => { p.items[0].records[0].raw.description = "\ud800"; }],
  ["record identity swap", p => { p.items[0].records[0].ref.record_id = "other"; }],
  ["pair/ref alignment", p => { p.items[0].records.reverse(); }],
  ["pair key", p => { p.items[0].pair.pair_key = "bad"; }],
  ["raw/proposal pair", p => { p.items[0].proposal.pair.pair_generation = "pg1:"+"f".repeat(64); }],
  ["missing evidence", p => { p.items[0].evidence.pop(); }],
  ["duplicate evidence", p => { p.items[0].evidence.push(p.items[0].evidence[0]); }],
  ["foreign pointer domain", p => { p.items[0].evidence[0].pointer.source_type = "saved_web"; }],
  ["false checked excerpt", p => { p.items[0].evidence[0].resolution.excerpt = "invented"; }],
  ["false raw quote", p => { p.items[0].evidence[0].pointer.locator.quote = "invented"; }],
  ["missing support metadata", p => { p.items[0].proposal.judgment.p_same = .9; }],
  ["relatedness as decision", p => { p.items[0].proposal.proposed_outcome = "merge"; }],
  ["failed judgment with signal", p => { p.items[0].proposal.judgment.status = "error"; }],
  ["impossible possible count", p => { p.report.possible_pairs = 2; }],
  ["cap accounting", p => { p.report.global_cap_drops = 1; }],
  ["incomplete catalog", p => { p.catalogs[0].row_count_complete = false; }],
  ["namespace collision", p => { p.catalogs[1].dataset_id = p.catalogs[0].dataset_id; }],
  ["invalid offset", p => { p.offset = 2; }],
  ["invalid limit", p => { p.limit = 101; }],
  ["false next offset", p => { p.next_offset = 1; }],
  ["false truncation", p => { p.preview_truncated = true; }],
  ["unsupported count mismatch", p => { p.unsupported_records_on_page = 1; }],
]) test(`reject ${name}`, () => { const value = source(); mutate(value); assert.throws(() => parse(value)); });

test("probability is imported metadata and never becomes human or journal capability", () => {
  const value = source(), judgment = value.items[0].proposal.judgment;
  judgment.p_same = .999;
  judgment.calibration = { dev_manifest_sha256:"a".repeat(64), config_sha256:"b".repeat(64), support_count:20 };
  value.items[0].proposal.proposed_outcome = "merge";
  const view = parse(value);
  assert.equal(view.items[0].pSame, .999);
  assert.equal(view.items[0].outcome, "merge");
  assert.equal("human_authority" in view, false);
  assert.equal("journal_write_authority" in view, false);
});
test("malformed UTF8 and JSON refused", () => {
  assert.throws(() => parseReviewPage(new Uint8Array([255])));
  assert.throws(() => parseReviewPage(new TextEncoder().encode("{")));
});
