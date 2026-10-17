import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { compareLocally, localViewerAvailable } from "../src/localComparison.ts";

const page = readFileSync(new URL("./fixtures/review-page.json", import.meta.url));
const header = '"id","name","brand","size","unit","price","category","description"\r\n';
// Exact authored original bytes used by the independent Python fixture producer.
const catalog = (id, brand) => new File([header + `"${id}","Café Tea","${brand}","100","g","1","Drinks","<script>literal text</script>"\r\n`], `${id}.csv`);
const input = () => ({ port: "20801", token: "T".repeat(32), a: catalog("one", "Acme"), b: catalog("two", "Other") });
const status = () => new Response(JSON.stringify({ kind: "local_read_only_comparison_service", upload_encoded_byte_limit: 20*1024*1024, human_rulings_available: false, provider_dispatch_available: false }), { headers: {"Content-Type":"application/json"} });
const options = fetcher => ({ origin: "http://127.0.0.1:20800", signal: new AbortController().signal, fetcher });

test("real producer page and exact original CSV bytes survive browser wire without authority", async () => {
  const selected = input(), calls = [];
  const output = await compareLocally(selected, options(async (url, init) => {
    calls.push({url, init});
    return calls.length === 1 ? status() : new Response(page, {headers:{"Content-Type":"application/json"}});
  }));
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "http://127.0.0.1:20801/api/status");
  assert.equal(calls[0].init.headers, undefined, "no token before service capability check");
  assert.equal(calls[1].url, "http://127.0.0.1:20801/api/comparisons");
  const body = JSON.parse(calls[1].init.body);
  assert.deepEqual(Buffer.from(body.catalog_a_base64, "base64"), Buffer.from(await selected.a.arrayBuffer()));
  assert.deepEqual(Buffer.from(body.catalog_b_base64, "base64"), Buffer.from(await selected.b.arrayBuffer()));
  assert.equal(body.method, "rules"); assert.equal(body.schema_version, 1);
  assert.equal(calls[1].init.credentials, "omit"); assert.equal(calls[1].init.redirect, "error");
  assert.ok(!calls.some(call => call.url.includes(selected.token)));
  assert.deepEqual(Buffer.from(await output.arrayBuffer()), page);
});
for (const [name, change, origin] of [
  ["external page", x=>x, "https://example.com"],
  ["deceptive localhost", x=>x, "http://localhost.evil.test:20800"],
  ["arbitrary URL as port", x=>({...x,port:"http://evil.test"})],
  ["zero port", x=>({...x,port:"0"})],
  ["invalid token", x=>({...x,token:"bad"})],
  ["missing catalog", x=>({...x,a:null})],
  ["size before read", x=>({...x,a:{size:20*1024*1024,arrayBuffer:()=>assert.fail("oversize file read")}})],
]) test(`${name} refuses before file reads or any network effect`, async () => {
  await assert.rejects(compareLocally(change(input()), {...options(()=>assert.fail("network reached")), ...(origin?{origin}:{})}));
});
test("unknown local service refuses before sending CSVs or token", async () => {
  let calls=0;
  await assert.rejects(compareLocally(input(), options(async()=>{calls++;return new Response('{}',{headers:{"Content-Type":"application/json"}})})), /supported local comparison/);
  assert.equal(calls,1);
});
for (const code of [400,403,413,503]) test(`HTTP${code} has a bounded helpful error without remote body or token`,async()=>{
  let calls=0;
  await assert.rejects(compareLocally(input(),options(async()=>++calls===1?status():new Response('sensitive remote text',{status:code}))), error=>!error.message.includes('sensitive')&&!error.message.includes('T'.repeat(32)));
});

test("CORS-opaque token refusal gives actionable guidance without disclosing remote text",async()=>{
  // Native Chromium hides an auth rejection without CORS headers as a fetch TypeError.
  let calls=0;
  await assert.rejects(compareLocally(input(),options(async()=>{
    if(++calls===1)return status();
    throw new TypeError('sensitive remote text');
  })),error=>error.message.includes('review token')&&error.message.includes('allowed page origin')&&!error.message.includes('sensitive')&&!error.message.includes('T'.repeat(32)));
  assert.equal(calls,2);
});
test("valid but foreign source page is refused instead of silently relabelled",async()=>{
  let calls=0;const selected=input();selected.a=catalog("one","Changed");
  await assert.rejects(compareLocally(selected,options(async()=>++calls===1?status():new Response(page,{headers:{"Content-Type":"application/json"}}))), /does not match/);
});
test("actual response bytes are bounded even without Content-Length",async()=>{
  let calls=0;
  await assert.rejects(compareLocally(input(),options(async()=>++calls===1?status():new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array(20*1024*1024+1));c.close()}}),{headers:{"Content-Type":"application/json"}}))), /too large/);
});
test("abort propagates to in-flight fetch and cannot issue upload",async()=>{
  const controller=new AbortController();let requestSignal,calls=0;
  const promise=compareLocally(input(),{origin:"http://127.0.0.1:20800",signal:controller.signal,fetcher:async(url,init)=>{calls++;requestSignal=init.signal;return new Promise((resolve,reject)=>init.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}))}});
  controller.abort();await assert.rejects(promise,{name:'AbortError'});assert.equal(calls,1);assert.ok(requestSignal.aborted);
});
test("loopback HTTP availability excludes file, hosted HTTPS and credential/query origins",()=>{
  assert.ok(localViewerAvailable("http://127.0.0.1:20800"));assert.ok(localViewerAvailable("http://localhost:20800"));
  for(const origin of ["file://", "https://localhost:20800", "http://user@localhost:20800", "http://localhost:20800/?x=1"])assert.equal(localViewerAvailable(origin),false);
});

for (const expiredDigest of [1, 2]) test(`timeout during source digest ${expiredDigest} cannot return a successful preview`, async t => {
  // Regression: network completed, but the internal timer fired while WebCrypto
  // was pending. That expired operation must not become an importable preview.
  let expire, digests = 0, requests = 0;
  t.mock.method(globalThis, "setTimeout", callback => { expire = callback; return undefined; });
  const digest = crypto.subtle.digest.bind(crypto.subtle);
  t.mock.method(crypto.subtle, "digest", async (...args) => {
    const result = await digest(...args);
    if (++digests === expiredDigest) expire();
    return result;
  });
  await assert.rejects(compareLocally(input(), options(async () => ++requests === 1 ? status() : new Response(page, { headers: { "Content-Type": "application/json" } }))), /timed out/);
  assert.equal(requests, 2);
  assert.equal(digests, expiredDigest, "expired comparison starts no further source digest");
});

test("explicit cancellation during a timed-out digest retains AbortError semantics", async t => {
  let expire, requests = 0;
  const controller = new AbortController();
  t.mock.method(globalThis, "setTimeout", callback => { expire = callback; return undefined; });
  const digest = crypto.subtle.digest.bind(crypto.subtle);
  t.mock.method(crypto.subtle, "digest", async (...args) => {
    const result = await digest(...args);
    expire(); controller.abort();
    return result;
  });
  await assert.rejects(compareLocally(input(), {
    ...options(async () => ++requests === 1 ? status() : new Response(page, { headers: { "Content-Type": "application/json" } })),
    signal: controller.signal,
  }), { name: "AbortError" });
});
