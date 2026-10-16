/** React/DOM integration controls. Dialog methods are explicit jsdom fakes, not native-browser proof. */
import assert from "node:assert/strict";
import { beforeEach, afterEach, after, test } from "node:test";
import { readFileSync, readdirSync, mkdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";
import { transformWithOxc } from "vite";
import { JSDOM } from "jsdom";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const cache = join(repo, "node_modules", ".cache", "s4-dom-tests");
mkdirSync(cache, { recursive: true });
const compiled = mkdtempSync(join(cache, "run-"));
writeFileSync(join(compiled, "package.json"), '{"type":"module"}');
for (const name of readdirSync(join(repo, "web/src"))) {
  if (!/\.tsx?$/.test(name)) continue;
  const source = readFileSync(join(repo, "web/src", name), "utf8");
  const result = await transformWithOxc(source, join(repo, "web/src", name), { target: "es2022", jsx: { runtime: "automatic" } });
  // Browser source uses extensionless relative imports; make the temporary Node modules explicit.
  const code = result.code.replace(/(from\s+["'])(\.\/[^"']+)(["'])/g,
    (_, before, specifier, after) => before + specifier.replace(/\.tsx?$/, "") + ".js" + after)
    .replace(/(from\s+["'])(\.\.\/[^"']+)(["'])/g,
      (_, before, specifier, after) => before + pathToFileURL(resolve(repo, "web/src", specifier)).href + after);
  writeFileSync(join(compiled, name.replace(/\.tsx?$/, ".js")), code);
}
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://127.0.0.1:20800/", pretendToBeVisual: true });
for (const name of ["window", "document", "HTMLElement", "HTMLInputElement", "HTMLTextAreaElement", "HTMLDialogElement", "Element", "Node", "Event", "MouseEvent", "KeyboardEvent", "getComputedStyle"]) {
  Object.defineProperty(globalThis, name, { value: typeof dom.window[name] === "function" && name === "getComputedStyle" ? dom.window[name].bind(dom.window) : dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let nativeConfirms = 0;
dom.window.confirm = () => { nativeConfirms++; throw new Error("blocking native confirm forbidden in these integration controls"); };
// jsdom does not implement native showModal/focus trapping. Application keyboard/focus behavior is exercised below.
dom.window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
dom.window.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { App } = await import(pathToFileURL(join(compiled, "App.js")).href);
// Avoid inspecting React's cyclic private fields when a DOM identity assertion fails.
const equal = (actual, expected, message) => {
  if (actual instanceof dom.window.Element || expected instanceof dom.window.Element) {
    assert.ok(actual === expected, message ?? `DOM identity differs: ${actual?.tagName ?? actual} / ${expected?.tagName ?? expected}`);
  } else assert.equal(actual, expected, message);
};
const fixture = JSON.parse(readFileSync(new URL("./fixtures/ruling-packet.json", import.meta.url), "utf8"));
const bytes = value => new TextEncoder().encode(typeof value === "string" ? value : JSON.stringify(value));
const file = (value, name = "incoming.json") => {
  const data = bytes(value);
  return { name, size: data.length, arrayBuffer: async () => data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) };
};
const pendingFile = () => {
  let finish;
  const value = { name: "slow.json", size: 100, arrayBuffer: () => new Promise(resolve => { finish = resolve; }) };
  return { value, finish: value => { const data = bytes(value); finish(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)); } };
};
let root, container, blobs, downloads;
const originalCreateURL = URL.createObjectURL, originalRevokeURL = URL.revokeObjectURL;
const originalFetch = globalThis.fetch;
URL.createObjectURL = blob => { const id = `blob:test-${blobs.size}`; blobs.set(id, blob); return id; };
URL.revokeObjectURL = () => {};
dom.window.HTMLAnchorElement.prototype.click = function () { if (this.download) downloads.push(blobs.get(this.href)); };
const button = name => [...container.querySelectorAll("button")].find(node =>
  node.textContent === name && (!node.closest("dialog") || node.closest("dialog").open));
const reason = () => container.querySelector("textarea");
const dialog = () => container.querySelector("dialog[open]");
async function click(name) { const node = button(name); assert.ok(node, name); await act(async () => { node.click(); }); }
async function upload(value, decisions = false) {
  const input = container.querySelectorAll('input[type="file"]')[decisions ? 1 : 0];
  Object.defineProperty(input, "files", { value: [value], configurable: true });
  await act(async () => { input.dispatchEvent(new dom.window.Event("change", { bubbles: true })); });
}
async function text(value) {
  const node = reason(); assert.ok(node);
  await act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, "value").set.call(node, value);
    node.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
}
async function key(node, value, options = {}) {
  const event = new dom.window.KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true, ...options });
  await act(async () => { node.dispatchEvent(event); });
  return event;
}
async function savedDraft() {
  await click("Download decisions");
  return Buffer.from(await downloads.at(-1).arrayBuffer());
}
async function drafted() {
  await upload(file(fixture, "packet.json"));
  await click("Match"); await text("Current reason: preserve every byte.");
}
beforeEach(async () => {
  blobs = new Map(); downloads = []; nativeConfirms = 0;
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container); await act(async () => root.render(createElement(App)));
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove();
  globalThis.fetch = originalFetch;
  assert.equal(nativeConfirms, 0, "the safety flow must not invoke a blocking native prompt");
});

const apiPage = readFileSync(new URL("./fixtures/review-page.json", import.meta.url));
const csvHeader = '"id","name","brand","size","unit","price","category","description"\r\n';
const apiCSV = (id, brand) => new File([csvHeader + `"${id}","Café Tea","${brand}","100","g","1","Drinks","<script>literal text</script>"\r\n`], `${id}.csv`);
const apiStatus = () => new Response(JSON.stringify({kind:"local_read_only_comparison_service",upload_encoded_byte_limit:20*1024*1024,human_rulings_available:false,provider_dispatch_available:false}),{headers:{"Content-Type":"application/json"}});
async function apiInput(selector, value) {
  const node=container.querySelector(selector);assert.ok(node,selector);
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,"value").set.call(node,value);node.dispatchEvent(new dom.window.Event("input",{bubbles:true}));});
}
async function prepareAPI() {
  await click("Connect local service");
  await apiInput('input[inputmode="numeric"]',"20801");await apiInput('input[type="password"]',"T".repeat(32));
  const inputs=container.querySelectorAll('[data-local-comparison] input[type="file"]');
  for(const [index, value] of [apiCSV("one","Acme"),apiCSV("two","Other")].entries()){
    Object.defineProperty(inputs[index],"files",{value:[value],configurable:true});
    await act(async()=>inputs[index].dispatchEvent(new dom.window.Event("change",{bubbles:true})));
  }
}
async function settleAPI() { await act(async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setTimeout(resolve,5));}); }
test("local API preview enters the existing validated report path without decision controls",async()=>{
  let calls=0;globalThis.fetch=async url=>{calls++;return url.endsWith("/api/status")?apiStatus():new Response(apiPage,{headers:{"Content-Type":"application/json"}});};
  await prepareAPI();await click("Compare catalogs");await settleAPI();
  assert.equal(calls,2);assert.ok(container.textContent.includes("Café Tea"));assert.equal(button("Download decisions"),undefined);assert.equal(dialog(),null);
});
test("failed API upload preserves the existing exact draft bytes",async()=>{
  await drafted();const before=await savedDraft();let calls=0;
  globalThis.fetch=async()=>++calls===1?apiStatus():new Response("untrusted response body",{status:400});
  await prepareAPI();await click("Compare catalogs");await settleAPI();
  assert.ok(container.querySelector('[role="alert"]').textContent.includes("refused these catalogs"));
  assert.deepEqual(await savedDraft(),before);assert.equal(dialog(),null);
});
test("API response asks before replacing a draft and Keep preserves exact bytes and selection",async()=>{
  await drafted();const before=await savedDraft();await prepareAPI();const current=reason();current.focus();current.setSelectionRange(2,9);
  globalThis.fetch=async url=>url.endsWith("/api/status")?apiStatus():new Response(apiPage,{headers:{"Content-Type":"application/json"}});
  await click("Compare catalogs");await settleAPI();assert.ok(dialog());equal(reason(),current);await click("Keep current draft");
  equal(document.activeElement,current);assert.equal(current.selectionStart,2);assert.equal(current.selectionEnd,9);assert.deepEqual(await savedDraft(),before);
  await click("Compare catalogs");await settleAPI();assert.ok(dialog());await click("Replace current draft");assert.equal(reason(),null);assert.equal(button("Download decisions"),undefined);
});
test("draft edit aborts an older API load and a late response cannot clear or prompt for that draft",async()=>{
  await drafted();await prepareAPI();let calls=0,finish,requestSignal;
  globalThis.fetch=async(url,init)=>{if(++calls===1)return apiStatus();requestSignal=init.signal;return new Promise(resolve=>{finish=resolve});};
  await click("Compare catalogs");await settleAPI();assert.ok(finish);await text("Newer draft wins over older API response.");const before=await savedDraft();
  assert.ok(requestSignal.aborted);await act(async()=>finish(new Response(apiPage,{headers:{"Content-Type":"application/json"}})));await settleAPI();
  assert.equal(dialog(),null);assert.deepEqual(await savedDraft(),before);assert.equal(reason().value,"Newer draft wins over older API response.");
});
after(() => {
  dom.window.close(); URL.createObjectURL = originalCreateURL; URL.revokeObjectURL = originalRevokeURL;
  const target = resolve(compiled), base = resolve(cache), rel = relative(base, target);
  assert.ok(rel && !rel.startsWith(".." + sep) && !rel.startsWith(sep) && target.startsWith(base + sep));
  rmSync(target, { recursive: true });
});

test("cancel, Escape and default Enter preserve actual draft wire bytes, text, focus and selection", async () => {
  await drafted(); const before = await savedDraft();
  for (const method of ["click", "Escape", "Enter"]) {
    const current = reason(); current.focus(); current.setSelectionRange(3, 11);
    await upload(file(fixture.page, "review-page.json"));
    assert.ok(dialog()); equal(reason(), current, "current draft must remain mounted");
    equal(document.activeElement, button("Keep current draft"));
    if (method === "click") await click("Keep current draft"); else await key(document.activeElement, method);
    equal(dialog(), null); equal(document.activeElement, current);
    equal(current.selectionStart, 3); equal(current.selectionEnd, 11);
    equal(current.value, "Current reason: preserve every byte.");
    assert.deepEqual(await savedDraft(), before, "cancel must preserve operation UUID/time/reason, not reconstruct a lookalike draft");
  }
});

test("only explicit accept of validated report replaces; Tab/Shift-Tab and Enter are deliberate", async () => {
  await drafted(); const prior = reason(); prior.focus();
  await upload(file(fixture.page, "valid-review.json"));
  assert.ok(dialog()); equal(reason(), prior);
  await key(document.activeElement, "Tab"); equal(document.activeElement, button("Replace current draft"));
  await key(document.activeElement, "Tab", { shiftKey: true }); equal(document.activeElement, button("Keep current draft"));
  const modified = await key(document.activeElement, "Tab", { ctrlKey: true }); equal(modified.defaultPrevented, false);
  await key(document.activeElement, "Tab"); await key(document.activeElement, "Enter");
  equal(dialog(), null); equal(reason(), null); equal(button("Download decisions"), undefined);
  equal(document.activeElement, container.querySelector('input[type="file"]'));
});

test("malformed, unreadable and oversized incoming reports never erase a live draft or ask to discard it", async () => {
  await drafted(); const before = await savedDraft(); const current = reason();
  await upload(file('{"kind":', "bad.json"));
  equal(dialog(), null); assert.ok(container.querySelector('[role="alert"]')); equal(reason(), current);
  assert.deepEqual(await savedDraft(), before);
  await upload({ name: "unreadable.json", size: 10, arrayBuffer: async () => { throw new Error("PRIVATE_PATH_CANARY"); } });
  equal(dialog(), null); assert.ok(container.textContent.includes("Could not read this file"));
  equal(container.textContent.includes("PRIVATE_PATH_CANARY"), false); assert.deepEqual(await savedDraft(), before);
  let read = false;
  await upload({ name: "large.json", size: 20 * 1024 * 1024 + 1, arrayBuffer: async () => { read = true; return new ArrayBuffer(0); } });
  equal(read, false); equal(dialog(), null); assert.deepEqual(await savedDraft(), before);
});

test("a stale slow selection cannot replace a newer report or publish a stale error", async () => {
  const slow = pendingFile(); await upload(slow.value);
  await upload(file(fixture)); assert.ok(button("Download decisions"));
  await act(async () => { slow.finish('{"kind":'); });
  assert.ok(button("Download decisions")); equal(container.querySelector('[role="alert"]'), null); equal(dialog(), null);
});

test("editing the real draft while an incoming read is pending invalidates that read", async () => {
  await drafted(); const slow = pendingFile(); await upload(slow.value);
  await text("A later real edit."); const current = await savedDraft();
  await act(async () => { slow.finish(fixture.page); });
  equal(dialog(), null); equal(reason().value, "A later real edit."); assert.deepEqual(await savedDraft(), current);
});

test("decision-file replacement validates packet binding before consent; cancel keeps bytes and accept alone changes them", async () => {
  await drafted(); const before = await savedDraft();
  const replacement = JSON.parse(before); replacement.operations[0].operation_id = "00000000-0000-4000-8000-000000000022";
  replacement.operations[0].verdict = "negative"; replacement.operations[0].reason = "Validated incoming draft.";
  await upload(file(replacement, "decisions.json"), true); assert.ok(dialog());
  await click("Keep current draft"); assert.deepEqual(await savedDraft(), before);
  await upload(file(replacement, "decisions.json"), true); await click("Replace current draft");
  equal(reason().value, replacement.operations[0].reason);
  const accepted = await savedDraft(); equal(JSON.parse(accepted).operations[0].verdict, "negative");
  equal(JSON.parse(accepted).operations[0].operation_id, replacement.operations[0].operation_id);
  const foreign = structuredClone(replacement); foreign.packet_id = "rp1:" + "f".repeat(64);
  await upload(file(foreign), true); equal(dialog(), null); assert.ok(container.querySelector('[role="alert"]'));
  assert.deepEqual(await savedDraft(), accepted);
});

test("a new decision invalidates an in-flight decisions-file import", async () => {
  await drafted(); const slow = pendingFile(); await upload(slow.value, true);
  await click("Different"); await text("Later choice wins."); const current = await savedDraft();
  await act(async () => { slow.finish(JSON.parse(current)); });
  equal(dialog(), null); assert.deepEqual(await savedDraft(), current);
  equal(JSON.parse(current).operations[0].verdict, "negative");
});
