// Structural transport only. Python retains digest, current-source, CAS and journal authority.
import packetSchema from "../../schemas/review/ruling-packet.schema.json" with { type: "json" };
import draftSchema from "../../schemas/review/ruling-draft.schema.json" with { type: "json" };
import { MAX_REPORT_BYTES, array, object, sha, text } from "./inspection.ts";
import { parseReviewPage, type ReviewPage } from "./reviewPage.ts";

type Row = Record<string, unknown>;
type Shape = { properties: Record<string, unknown> };
const packetDefs = packetSchema.$defs as unknown as Record<string, Shape>;
const draftDefs = draftSchema.$defs as unknown as Record<string, Shape>;
function fail(): never { throw new Error("Unsupported or inconsistent review packet or decisions."); }
function closed(value: unknown, keys: string[]): Row {
  const row = object(value);
  if (Object.keys(row).some(key => !keys.includes(key))) return fail();
  return row;
}
function shape(value: unknown, definition: string, draft = false): Row {
  return closed(value, Object.keys((draft ? draftDefs : packetDefs)[definition].properties));
}
function opaque(value: unknown): string {
  const result = text(value, 2048);
  if ([...result].length > 512) return fail();
  return result;
}
function nullable(value: unknown): string | null { return value === null ? null : opaque(value); }
function packetId(value: unknown): string {
  const result = text(value, 68);
  if (!/^rp1:[0-9a-f]{64}$/.test(result)) return fail();
  return result;
}

// Probe only root string tags before any JSON container allocation. Legacy readers
// retain their existing limits; every ruling packet still receives the strict full scan.
export function hasRulingPacketTag(bytes: Uint8Array): boolean {
  if (bytes.byteLength > MAX_REPORT_BYTES) return fail();
  let source: string;
  try { source = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { return fail(); }
  let depth = 0, keyExpected = true, lastKey: string | null = null, kindValue = false;
  for (let i = 0; i < source.length; i++) {
    const char = source[i];
    if (char === '"') {
      const start = i;
      for (i++; i < source.length; i++) {
        if (source[i] === "\\") i++;
        else if (source[i] === '"') break;
      }
      if (depth === 1 && (keyExpected || kindValue)) {
        let token: unknown;
        try { token = JSON.parse(source.slice(start, i + 1)); } catch { return fail(); }
        if (typeof token !== "string") return fail();
        if (kindValue && token === "product_ruling_packet") return true;
        if (keyExpected) { lastKey = token; keyExpected = false; }
        kindValue = false;
      }
    } else if (char === "{" || char === "[") depth++;
    else if (char === "}" || char === "]") depth--;
    else if (depth === 1 && char === ",") { keyExpected = true; lastKey = null; kindValue = false; }
    else if (depth === 1 && char === ":") kindValue = lastKey === "kind";
  }
  return false;
}

export function readRulingJSON(bytes: Uint8Array): unknown {
  if (bytes.byteLength > MAX_REPORT_BYTES || (bytes[0] === 239 && bytes[1] === 187 && bytes[2] === 191)) return fail();
  try {
    const source = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const stack: { kind: string; key: boolean; lastKey: string | null; names: Set<string> }[] = [];
    let separators = 0;
    for (let i = 0; i < source.length; i++) {
      const char = source[i], top = stack.at(-1);
      if (char === '"') {
        const start = i;
        for (i++; i < source.length; i++) {
          if (source[i] === "\\") i++;
          else if (source[i] === '"') break;
        }
        if (top?.kind === "{" && top.key) {
          const key: unknown = JSON.parse(source.slice(start, i + 1));
          if (typeof key !== "string" || top.names.has(key)) return fail();
          top.names.add(key); top.lastKey = key; top.key = false;
        }
      } else if (char === "{" || char === "[") {
        stack.push({ kind: char, key: true, lastKey: null, names: new Set() });
        if (stack.length > 32) return fail();
      } else if (char === "}" || char === "]") {
        if (stack.pop()?.kind !== (char === "}" ? "{" : "[")) return fail();
      } else if (char === "," || char === ":") {
        if (++separators > 200000) return fail();
        if (char === "," && top?.kind === "{") top.key = true;
        if (char === ":" && top?.lastKey === "schema_version") {
          let start = i + 1;
          while (/\s/.test(source[start] ?? "") && start < source.length) start++;
          if (source[start] !== "1") return fail();
          let end = start + 1;
          while (/\s/.test(source[end] ?? "") && end < source.length) end++;
          if (source[end] !== "," && source[end] !== "}") return fail();
        }
      }
    }
    return JSON.parse(source);
  } catch { return fail(); }
}

export type PacketEntryView = { key: string; head: string | null; retractable: string | null; issue: string | null };
export type RulingPacket = { id: string; namespace: string; journalStatus: string; page: ReviewPage; entries: PacketEntryView[] };
export function parseRulingPacket(bytes: Uint8Array): RulingPacket {
  const row = closed(readRulingJSON(bytes), Object.keys(packetSchema.properties));
  if (row.schema_version !== 1 || row.kind !== "product_ruling_packet" || row.authority !== "provisional_review_only") return fail();
  const id = packetId(row.packet_id), namespace = opaque(row.namespace);
  const journalStatus = text(row.journal_status, 20);
  if (!["clean", "repair_required", "limit_exceeded"].includes(journalStatus)) return fail();
  const page = parseReviewPage(new TextEncoder().encode(JSON.stringify(row.page)));
  const entries = array(row.entries, 100).map((value, index) => {
    const entry = shape(value, "PacketEntry"), item = page.items[index];
    if (!item || entry.pair_key !== item.key) return fail();
    const head = nullable(entry.observed_head_event_id), retractable = nullable(entry.retractable_event_id);
    const issue = entry.issue_code === null ? null : text(entry.issue_code, 96);
    if (issue === "canonical_identity_unsupported") {
      if (item.records.every(record => [...record.ref.record_id].length <= 512)) return fail();
      if (entry.subject !== null || entry.generation !== null || head !== null || retractable !== null) return fail();
    } else {
      if (issue !== (journalStatus === "clean" ? null : "journal_read_only")) return fail();
      const subject = shape(entry.subject, "PairSubject"), generation = shape(entry.generation, "PairGeneration");
      if (subject.schema_version !== 1 || subject.kind !== "pair" || subject.pair_key !== item.key ||
          generation.schema_version !== 1 || generation.kind !== "pair" || generation.profile !== "product-raw-fields-v1" || generation.pair_generation !== item.generation) return fail();
      const refs = array(subject.refs, 2), hashes = array(generation.raw_fingerprints, 2).map(sha);
      if (refs.length !== 2 || hashes.length !== 2) return fail();
      refs.forEach((value, i) => {
        const ref = closed(value, ["dataset_id", "record_id"]), actual = item.records[i].ref;
        if (opaque(ref.dataset_id) !== actual.dataset_id || opaque(ref.record_id) !== actual.record_id || hashes[i] !== actual.content_hash) return fail();
      });
      if (retractable !== null && (retractable !== head || issue !== null)) return fail();
    }
    return { key: item.key, head, retractable, issue };
  });
  if (entries.length !== page.items.length) return fail();
  return { id, namespace, journalStatus, page, entries };
}

export type Verdict = "positive" | "negative" | "unsure";
type CommonOperation = { operation_id: string; pair_key: string; observed_head_event_id: string | null; at_utc: string; reason: string };
export type DraftOperation = CommonOperation & ({ action: "decide"; verdict: Verdict } | { action: "retract"; target_event_id: string });
export type RulingDraft = { schema_version: 1; kind: "product_ruling_draft"; packet_id: string; operations: DraftOperation[] };
function uuid4(value: unknown): string {
  const id = text(value, 36);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id)) return fail();
  return id;
}
function utc(value: unknown): string {
  const date = text(value, 40);
  if (date.startsWith("0000") || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(date) || !Number.isFinite(Date.parse(date))) return fail();
  const normalized = new Date(date).toISOString();
  if (normalized.slice(0, 19) !== date.slice(0, 19)) return fail();
  return date;
}
function reason(value: unknown): string { return value === "" ? "" : text(value, 4096); }
export function parseRulingDraft(packet: RulingPacket, bytes: Uint8Array): RulingDraft {
  const row = closed(readRulingJSON(bytes), Object.keys(draftSchema.properties));
  if (row.schema_version !== 1 || row.kind !== "product_ruling_draft" || row.packet_id !== packet.id) return fail();
  const pairs = new Set<string>(), ids = new Set<string>();
  const operations = array(row.operations, 100).map(value => {
    const initial = object(value);
    if (initial.action !== "decide" && initial.action !== "retract") return fail();
    const item = shape(value, initial.action === "decide" ? "DecideOperation" : "RetractOperation", true);
    const key = text(item.pair_key, 68), entry = packet.entries.find(entry => entry.key === key);
    const id = uuid4(item.operation_id), head = nullable(item.observed_head_event_id);
    if (!entry || entry.issue !== null || packet.journalStatus !== "clean" || head !== entry.head || pairs.has(key) || ids.has(id)) return fail();
    pairs.add(key); ids.add(id);
    const common = { operation_id: id, pair_key: key, observed_head_event_id: head, at_utc: utc(item.at_utc), reason: reason(item.reason) };
    if (item.action === "decide") {
      if (typeof item.verdict !== "string" || !["positive", "negative", "unsure"].includes(item.verdict)) return fail();
      return { ...common, action: "decide" as const, verdict: item.verdict as Verdict };
    }
    const target = opaque(item.target_event_id);
    if (target !== entry.retractable) return fail();
    return { ...common, action: "retract" as const, target_event_id: target };
  });
  return { schema_version: 1, kind: "product_ruling_draft", packet_id: packet.id, operations };
}
export function emptyDraft(packet: RulingPacket): RulingDraft {
  return { schema_version: 1, kind: "product_ruling_draft", packet_id: packet.id, operations: [] };
}
export function encodeRulingDraft(packet: RulingPacket, draft: RulingDraft): Uint8Array {
  const bytes = new TextEncoder().encode(JSON.stringify(draft));
  parseRulingDraft(packet, bytes);
  return bytes;
}
export function chooseDraft(packet: RulingPacket, draft: RulingDraft, key: string,
  choice: Verdict | "retract" | "skip", note: string,
  clock: { uuid: () => string; now: () => string }): RulingDraft {
  encodeRulingDraft(packet, draft);
  const entry = packet.entries.find(entry => entry.key === key);
  if (!entry || entry.issue !== null || packet.journalStatus !== "clean") return fail();
  const remaining = draft.operations.filter(item => item.pair_key !== key);
  if (choice !== "skip") {
    const common = { operation_id: uuid4(clock.uuid()), pair_key: key, observed_head_event_id: entry.head, at_utc: utc(clock.now()), reason: reason(note) };
    const operation: DraftOperation = choice === "retract" ?
      { ...common, action: "retract", target_event_id: entry.retractable ?? fail() } :
      { ...common, action: "decide", verdict: choice };
    remaining.push(operation);
  }
  const result = { ...draft, operations: packet.entries.flatMap(entry => remaining.filter(item => item.pair_key === entry.key)) };
  return parseRulingDraft(packet, new TextEncoder().encode(JSON.stringify(result)));
}
