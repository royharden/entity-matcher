import { useEffect, useRef, useState } from "react";
import { MAX_REPORT_BYTES } from "./inspection";

export type FocusBookmark = {
  target: HTMLElement | null;
  start: number | null;
  end: number | null;
  direction: "forward" | "backward" | "none" | null;
};

export function bookmarkFocus(preferred: HTMLElement | null): FocusBookmark {
  const target = preferred?.isConnected ? preferred : document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const input = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ? target : null;
  return { target, start: input?.selectionStart ?? null, end: input?.selectionEnd ?? null, direction: input?.selectionDirection ?? null };
}

export function restoreFocus(bookmark: FocusBookmark | null, fallback: HTMLElement | null) {
  const target = bookmark?.target?.isConnected ? bookmark.target : fallback;
  target?.focus();
  if (bookmark && target === bookmark.target && bookmark.start !== null && bookmark.end !== null &&
      (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) {
    target.setSelectionRange(bookmark.start, bookmark.end, bookmark.direction ?? undefined);
  }
}

export function isDraftFocus(target: EventTarget): target is HTMLElement {
  return target instanceof HTMLElement && !target.closest("dialog") &&
    !(target instanceof HTMLInputElement && target.type === "file");
}

type PendingImport<T> = { value: T; name: string; ticket: number; focus: FocusBookmark };
type ImportOptions<T> = { parse: (bytes: Uint8Array) => T; hasDraft: () => boolean; commit: (value: T) => void; errorMessage?: string };

/** Validate before asking to replace. Stale reads and edits cannot silently commit. */
export function useValidatedImport<T>(options: ImportOptions<T>) {
  const latest = useRef(options);
  latest.current = options;
  const request = useRef(0);
  const [pending, setPending] = useState<PendingImport<T> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => () => { request.current++; }, []);

  function invalidate() {
    request.current++;
    setPending(null); setBusy(false); setError("");
  }
  function commit(value: T) {
    request.current++;
    setPending(null); setBusy(false); setError("");
    latest.current.commit(value);
  }
  async function open(file: File | undefined, focus: FocusBookmark) {
    if (!file) return;
    const ticket = ++request.current;
    setPending(null); setError(""); setBusy(true);
    try {
      if (file.size > MAX_REPORT_BYTES) throw new Error("Choose a report smaller than 20 MiB.");
      let buffer: ArrayBuffer;
      try { buffer = await file.arrayBuffer(); }
      catch { throw new Error("Could not read this file. Choose it again."); }
      const bytes = new Uint8Array(buffer);
      if (ticket !== request.current) return;
      const value = latest.current.parse(bytes);
      if (latest.current.hasDraft()) setPending({ value, name: file.name, ticket, focus });
      else commit(value);
    } catch (cause) {
      if (ticket === request.current) setError(latest.current.errorMessage ?? (cause instanceof Error ? cause.message : "Could not open the report."));
    } finally { if (ticket === request.current) setBusy(false); }
  }
  function cancel() {
    request.current++;
    setPending(null); setBusy(false);
  }
  function accept() {
    if (pending && pending.ticket === request.current) commit(pending.value);
  }
  return { open, pending, error, busy, cancel, accept, invalidate };
}
