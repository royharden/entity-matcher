import { MAX_REPORT_BYTES, object, readReportJSON } from "./inspection.ts";
import { parseReviewPage } from "./reviewPage.ts";

export type CatalogFile = Pick<File, "size" | "arrayBuffer">;
export type LocalComparison = { port: string; token: string; a: CatalogFile | null; b: CatalogFile | null };

export function localViewerAvailable(origin: string): boolean {
  try {
    const url = new URL(origin);
    return url.protocol === "http:" && (url.hostname === "127.0.0.1" || url.hostname === "localhost") && url.origin === origin;
  } catch { return false; }
}
function fail(message: string): never { throw new Error(message); }
async function responseBytes(response: Response, maximum: number): Promise<Uint8Array> {
  if (response.redirected || response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
    return fail("The local service returned an unsupported response.");
  }
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^[0-9]{1,10}$/.test(declared) || Number(declared) > maximum)) {
    return fail("The local service response is too large.");
  }
  if (!response.body) return fail("The local service returned an empty response.");
  const reader = response.body.getReader(), chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximum) return fail("The local service response is too large.");
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}
function httpError(status: number): never {
  if (status === 401 || status === 403) return fail("Check the local service port, review token and allowed page origin.");
  if (status === 413) return fail("These catalogs exceed the local service upload limit. Choose smaller files.");
  if (status === 400 || status === 422) return fail("The local service refused these catalogs. Check their CSV headers and values.");
  if (status === 429 || status === 503) return fail("The local service is busy. Try again after the current comparison finishes.");
  return fail("The local comparison failed. Check that the service is ready and try again.");
}
function base64(bytes: Uint8Array): string {
  // A multiple of three avoids padding inside concatenated chunks; no lossy text decoding.
  const parts: string[] = [];
  for (let start = 0; start < bytes.length; start += 32766) parts.push(btoa(String.fromCharCode(...bytes.subarray(start, start + 32766))));
  return parts.join("");
}

/** Consumer of the existing bounded read-only API; no new wire or journal authority. */
export async function compareLocally(input: LocalComparison, options: {
  origin: string; signal: AbortSignal; fetcher?: typeof fetch;
}): Promise<File> {
  const { port, token, a, b } = input;
  if (!localViewerAvailable(options.origin)) return fail("Open this viewer from an HTTP localhost address to use a local service.");
  if (!/^[0-9]{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) return fail("Enter the port used by your local comparison service.");
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(token)) return fail("Enter the review token supplied to your local comparison service.");
  if (!a || !b) return fail("Choose both CSV catalogs.");
  for (const file of [a, b]) if (!Number.isSafeInteger(file.size) || file.size < 0 || file.size > MAX_REPORT_BYTES) return fail("Choose smaller CSV files; the combined encoded upload must fit within 20 MiB.");
  const encodedSize = Math.ceil(a.size / 3) * 4 + Math.ceil(b.size / 3) * 4;
  const wire = { schema_version: 1, dataset_a: "a", dataset_b: "b", catalog_a_base64: "", catalog_b_base64: "", method: "rules" };
  if (encodedSize + new TextEncoder().encode(JSON.stringify(wire)).byteLength > MAX_REPORT_BYTES) return fail("Choose smaller CSV files; the combined encoded upload must fit within 20 MiB.");
  if (options.signal.aborted) throw new DOMException("Comparison cancelled.", "AbortError");
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  options.signal.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
  const url = `http://127.0.0.1:${Number(port)}`;
  const fetcher = options.fetcher ?? globalThis.fetch;
  async function request(path: string, init?: RequestInit) {
    let response: Response;
    try { response = await fetcher(url + path, { ...init, signal: controller.signal, redirect: "error", credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer" }); }
    catch {
      if (options.signal.aborted) throw new DOMException("Comparison cancelled.", "AbortError");
      return fail(timedOut ? "The local comparison timed out. Try smaller catalogs or check the service." : "Could not reach the local service. Check its port, review token and allowed page origin.");
    }
    if (!response.ok) return httpError(response.status);
    if (response.redirected || (response.url && new URL(response.url).origin !== url)) return fail("The local service returned an unexpected redirect.");
    return response;
  }
  try {
    const statusBytes = await responseBytes(await request("/api/status"), 16384);
    let status: ReturnType<typeof object>;
    try { status = object(readReportJSON(statusBytes)); } catch { return fail("This port does not provide the supported local comparison service."); }
    const limit = status.upload_encoded_byte_limit;
    if (status.kind !== "local_read_only_comparison_service" || status.human_rulings_available !== false || status.provider_dispatch_available !== false ||
        typeof limit !== "number" || !Number.isSafeInteger(limit) || limit < 1 || limit > MAX_REPORT_BYTES) return fail("This port does not provide the supported local comparison service.");
    const catalogs: Uint8Array[] = [];
    for (const file of [a, b]) {
      let buffer: ArrayBuffer;
      try { buffer = await file.arrayBuffer(); } catch { return fail("Could not read a CSV file. Choose the files again."); }
      if (buffer.byteLength !== file.size || controller.signal.aborted) {
        if (options.signal.aborted) throw new DOMException("Comparison cancelled.", "AbortError");
        return fail("The files changed or the comparison timed out. Choose them again.");
      }
      catalogs.push(new Uint8Array(buffer));
    }
    const body = JSON.stringify({ ...wire, catalog_a_base64: base64(catalogs[0]), catalog_b_base64: base64(catalogs[1]) });
    if (new TextEncoder().encode(body).byteLength > limit) return fail("These catalogs exceed the local service upload limit. Choose smaller files.");
    const bytes = await responseBytes(await request("/api/comparisons", { method: "POST", headers: { "Content-Type": "application/json", "X-Review-Token": token }, body }), MAX_REPORT_BYTES);
    let page: ReturnType<typeof parseReviewPage>;
    try { page = parseReviewPage(bytes); } catch { return fail("The local service returned an invalid review page."); }
    for (const [index, dataset] of ["a", "b"].entries()) {
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(catalogs[index])));
      if (controller.signal.aborted) throw new DOMException("Comparison interrupted.", "AbortError");
      const hex = Array.from(digest, value => value.toString(16).padStart(2, "0")).join("");
      if (page.catalogs.find(catalog => catalog.dataset === dataset)?.sha256 !== hex) return fail("The returned preview does not match the selected CSV files.");
    }
    if (controller.signal.aborted) throw new DOMException("Comparison interrupted.", "AbortError");
    return new File([new Uint8Array(bytes)], "local-comparison.json", { type: "application/json" });
  } catch (error) {
    if (options.signal.aborted) throw new DOMException("Comparison cancelled.", "AbortError");
    if (timedOut) return fail("The local comparison timed out. Try smaller catalogs or check the service.");
    if (error instanceof TypeError || (error instanceof DOMException && error.name === "AbortError")) return fail("The local connection was interrupted. Check the service and try again.");
    throw error;
  } finally { clearTimeout(timer); options.signal.removeEventListener("abort", abort); }
}
