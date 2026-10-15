import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chooseDraft, emptyDraft, encodeRulingDraft, hasRulingPacketTag, parseRulingDraft, parseRulingPacket, readRulingJSON } from "../src/rulingPacket.ts";

const fixture = readFileSync(new URL("./fixtures/ruling-packet.json", import.meta.url));
const source = () => JSON.parse(fixture.toString("utf-8"));
const bytes = value => new TextEncoder().encode(JSON.stringify(value));
const parse = value => parseRulingPacket(bytes(value));
const packet = () => parseRulingPacket(fixture);
let counter = 0;
const clock = { uuid: () => `00000000-0000-4000-8000-${(++counter).toString(16).padStart(12, "0")}`, now: () => "2026-10-15T00:00:00.000Z" };

test("root tag probe selects strict packet parsing before containers and preserves legacy routing", () => {
  assert.equal(hasRulingPacketTag(fixture), true);
  assert.equal(hasRulingPacketTag(bytes({ kind: "product_review_page", value: "product_ruling_packet" })), false);
  assert.equal(hasRulingPacketTag(bytes({ nested: { kind: "product_ruling_packet" }, stage: "candidates" })), false);
  assert.equal(hasRulingPacketTag(new TextEncoder().encode('{"ki\\u006ed":"product_ruling_packet"}')), true);
  const legacy = new TextEncoder().encode('{"nested":' + "[".repeat(33) + "0" + "]".repeat(33) + ',"stage":"candidates"}');
  assert.equal(hasRulingPacketTag(legacy), false);
  assert.throws(() => readRulingJSON(legacy));
  const duplicate = new TextEncoder().encode('{"kind":"product_ruling_packet","kind":"product_review_page"}');
  assert.equal(hasRulingPacketTag(duplicate), true);
  assert.throws(() => readRulingJSON(duplicate));
});

test("actual Python packet producer interoperates without granting source/journal/human authority", () => {
  const view = packet();
  assert.equal(view.entries.length, 1);
  assert.equal(view.page.items[0].records[0].raw.description, "<script>literal text</script>");
  assert.equal(view.page.items[0].evidence.length, 16);
  assert.equal(view.entries[0].head, null);
  assert.equal(view.entries[0].retractable, null);
  assert.equal("human_authority" in view, false);
  assert.equal("journal_write_authority" in view, false);
  const reported = source(); reported.packet_id = "rp1:" + "f".repeat(64);
  assert.equal(parse(reported).id, reported.packet_id); // digest remains unverified metadata here
});

for (const [name, mutate] of [
  ["human authority", p => { p.authority = "human"; }],
  ["extra actor", p => { p.actor_provenance = "human"; }],
  ["wrong packet id", p => { p.packet_id = "bad"; }],
  ["foreign pair", p => { p.entries[0].pair_key = "pk1:" + "f".repeat(64); }],
  ["missing entry", p => { p.entries = []; }],
  ["foreign subject", p => { p.entries[0].subject.refs[0].record_id = "other"; }],
  ["swapped subjects", p => { p.entries[0].subject.refs.reverse(); }],
  ["foreign generation", p => { p.entries[0].generation.pair_generation = "pg1:" + "f".repeat(64); }],
  ["wrong profile", p => { p.entries[0].generation.profile = "other"; }],
  ["changed fingerprint", p => { p.entries[0].generation.raw_fingerprints[0] = "f".repeat(64); }],
  ["false unsupported shape", p => { p.entries[0].issue_code = "canonical_identity_unsupported"; p.entries[0].subject = p.entries[0].generation = null; }],
  ["false journal status", p => { p.journal_status = "repair_required"; }],
  ["wrong retraction target", p => { p.entries[0].retractable_event_id = "missing"; }],
  ["extra path", p => { p.entries[0].path = "../CANARY"; }],
]) test(`reject packet ${name}`, () => { const value = source(); mutate(value); assert.throws(() => parse(value)); });

test("draft transitions use new UUIDs for edits, clear means no event, same packet reimports", () => {
  const p = packet(), key = p.entries[0].key;
  const first = chooseDraft(p, emptyDraft(p), key, "positive", "<script>literal =SUM(1,2)</script>", clock);
  assert.equal(first.operations[0].verdict, "positive");
  const second = chooseDraft(p, first, key, "negative", "changed", clock);
  assert.notEqual(first.operations[0].operation_id, second.operations[0].operation_id);
  assert.equal(first.operations[0].verdict, "positive");
  assert.deepEqual(parseRulingDraft(p, encodeRulingDraft(p, second)), second);
  assert.equal("actor_provenance" in second.operations[0], false);
  const before = counter;
  assert.equal(chooseDraft(p, second, key, "skip", "", clock).operations.length, 0);
  assert.equal(counter, before);
});

for (const [name, mutate] of [
  ["human claim", p => { p.operations[0].actor_provenance = "human"; }],
  ["sequence", p => { p.operations[0].sequence = 1; }],
  ["namespace override", p => { p.operations[0].namespace = "other"; }],
  ["head override", p => { p.operations[0].observed_head_event_id = "other"; }],
  ["foreign packet", p => { p.packet_id = "rp1:" + "f".repeat(64); }],
  ["UUID version", p => { p.operations[0].operation_id = "00000000-0000-1000-8000-000000000000"; }],
  ["reason byte bound", p => { p.operations[0].reason = "é".repeat(2049); }],
  ["surrogate", p => { p.operations[0].reason = "\ud800"; }],
  ["bad UTC", p => { p.operations[0].at_utc = "2026-10-15T00:00:00+01:00"; }],
  ["invalid date", p => { p.operations[0].at_utc = "2026-02-30T00:00:00Z"; }],
  ["zero year", p => { p.operations[0].at_utc = "0000-10-15T00:00:00Z"; }],
  ["duplicate operation", p => { p.operations.push(p.operations[0]); }],
  ["extra action payload", p => { p.operations[0].target_event_id = "extra"; }],
]) test(`reject draft ${name}`, () => {
  const p = packet();
  const value = chooseDraft(p, emptyDraft(p), p.entries[0].key, "unsure", "", clock);
  mutate(value); assert.throws(() => parseRulingDraft(p, bytes(value)));
});

test("reported existing head permits only its matching retraction; damaged packet remains readonly", () => {
  const value = source();
  value.entries[0].observed_head_event_id = value.entries[0].retractable_event_id = "observed-only";
  const p = parse(value);
  const result = chooseDraft(p, emptyDraft(p), p.entries[0].key, "retract", "undo", clock);
  assert.equal(result.operations[0].target_event_id, "observed-only");
  assert.equal(result.operations[0].action, "retract");
  const original = packet();
  assert.throws(() => chooseDraft(original, emptyDraft(original), original.entries[0].key, "retract", "", clock));
  value.journal_status = "repair_required"; value.entries[0].issue_code = "journal_read_only"; value.entries[0].retractable_event_id = null;
  const readonly = parse(value);
  assert.throws(() => chooseDraft(readonly, emptyDraft(readonly), readonly.entries[0].key, "positive", "", clock));
});

for (const [name, value] of [
  ["duplicate root", '{"schema_version":1,"schema_version":1}'],
  ["escaped duplicate", '{"schema_version":1,"schema_ver\\u0073ion":1}'],
  ["nested duplicate", '{"x":{"key":1,"key":2}}'],
  ["float version", '{"schema_version":1.0}'],
  ["exponent version", '{"schema_version":1e0}'],
  ["BOM", '\ufeff{}'], ["NaN", '{"x":NaN}'], ["Infinity", '{"x":Infinity}'],
  ["depth", "[".repeat(33) + "]".repeat(33)],
  ["structure", "[" + "0,".repeat(200001) + "0]"],
]) test(`strict transport refuses ${name} before accepting containers`, () => assert.throws(() => readRulingJSON(new TextEncoder().encode(value))));

test("strict transport refuses malformed UTF8 and byte excess; braces and commas inside strings remain text", () => {
  assert.throws(() => readRulingJSON(new Uint8Array([255])));
  assert.throws(() => readRulingJSON(new Uint8Array(20 * 1024 * 1024 + 1)));
  assert.deepEqual(readRulingJSON(bytes({ value: '{,"key":"literal"}' })), { value: '{,"key":"literal"}' });
});

test("actual TS draft bytes pass actual Python decoder/controller and real owner journal", () => {
  const p = packet(), result = chooseDraft(p, emptyDraft(p), p.entries[0].key, "positive", "TS interop", clock);
  const python = fileURLToPath(new URL(process.platform === "win32" ? "../../.venv/Scripts/python.exe" : "../../.venv/bin/python", import.meta.url));
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const code = [
    "import csv,io,json,sys,tempfile",
    "from pathlib import Path",
    "from entity_matcher.pipeline.ruling_packet import decode_packet,decode_draft",
    "from entity_matcher.pipeline.apply_draft import apply_draft",
    "from entity_matcher.pipeline.workspace import create_workspace",
    "from entity_matcher.shared.rulings.store import JournalStore",
    "p=decode_packet(Path('web/tests/fixtures/ruling-packet.json').read_bytes())",
    "def catalog(record):\n s=io.StringIO(newline='');w=csv.writer(s);w.writerow(('id','name','brand','size','unit','price','category','description'));w.writerow(tuple(getattr(record.raw,k) for k in ('id','name','brand','size','unit','price','category','description')));return s.getvalue().encode()",
    "records=p.page.items[0].records",
    "workspace=create_workspace(*(catalog(record) for record in records))",
    "d=decode_draft(sys.stdin.buffer.read())",
    "with tempfile.TemporaryDirectory(prefix='D4-ts-interop-') as directory:\n store=JournalStore(Path(directory));r=apply_draft(workspace,p,d,store,namespace='local-products');events=store.replay().events;assert r.issue_code is None and len(events)==1 and events[0].actor_provenance=='agent_provisional';print(json.dumps({'receipts':len(r.receipts),'human':r.human_admission_available,'provenance':events[0].actor_provenance}))",
  ].join("\n");
  const run = spawnSync(python, ["-c", code], { cwd: root, input: encodeRulingDraft(p, result), timeout: 20000 });
  assert.equal(run.status, 0, run.stderr.toString());
  assert.deepEqual(JSON.parse(run.stdout.toString()), { receipts: 1, human: false, provenance: "agent_provisional" });
});
