import { useRef, useState } from "react";
import { ReviewPageView } from "./ReviewPageView";
import { chooseDraft, emptyDraft, encodeRulingDraft, parseRulingDraft, type RulingDraft, type RulingPacket, type Verdict } from "./rulingPacket";
import { ReplacementDialog } from "./ReplacementDialog";
import { bookmarkFocus, isDraftFocus, useValidatedImport } from "./useValidatedImport";

export function RulingPacketView({ packet, onDraftPresentChange }: { packet: RulingPacket; onDraftPresentChange?: (present: boolean) => void }) {
  const [draft, setDraft] = useState(() => emptyDraft(packet));
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [help, setHelp] = useState(false);
  const currentDraft = useRef(draft);
  currentDraft.current = draft;
  const input = useRef<HTMLInputElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  const importer = useValidatedImport<RulingDraft>({
    parse: bytes => parseRulingDraft(packet, bytes),
    hasDraft: () => currentDraft.current.operations.length > 0,
    errorMessage: "Choose a valid decisions file for this exact packet.",
    commit(parsed) {
      currentDraft.current = parsed; setDraft(parsed);
      onDraftPresentChange?.(parsed.operations.length > 0); setError("");
      setMessage("Decisions loaded for this packet. They have not been applied.");
    },
  });
  function choose(key: string, choice: Verdict | "retract" | "skip", note = "") {
    importer.invalidate();
    try {
      const next = chooseDraft(packet, currentDraft.current, key, choice, note, {
        uuid: () => globalThis.crypto.randomUUID(), now: () => new Date().toISOString(),
      });
      currentDraft.current = next; setDraft(next); onDraftPresentChange?.(next.operations.length > 0); setError("");
      setMessage(choice === "skip" ? "Draft cleared; no event created." : "Draft updated. Apply the downloaded file locally to record it.");
    } catch { setError("Could not draft this decision. Check the reason length and packet state."); }
  }
  function download() {
    importer.invalidate();
    try {
      const bytes = encodeRulingDraft(packet, currentDraft.current);
      const blob = new Blob([new Uint8Array(bytes)], { type: "application/json" });
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = "entity-matcher-decisions.json";
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setError(""); setMessage("Download requested. Keep the decisions file to apply it locally; this page has not changed your journal.");
    } catch { setError("Could not download these decisions."); }
  }
  return <div onFocusCapture={event => { if (isDraftFocus(event.target)) lastFocus.current = event.target; }}>
    <section aria-labelledby="draft-heading">
      <span className="badge">Local review draft</span><h2 id="draft-heading">Review, download and apply</h2>
      <p>Mark a pair as a match, different or unsure. These are provisional decisions until you apply the downloaded file with the local comparison tool.</p>
      <p className="notice">This page cannot verify current catalogs or save to your journal. Imported claims and these controls provide no verified human admission.</p>
      {packet.journalStatus !== "clean" && <p role="alert" className="error">The packet reports a read-only journal ({packet.journalStatus}). New decisions are disabled.</p>}
      <div className="draft-toolbar"><strong>{draft.operations.length} drafted decisions</strong>
        <button onClick={download}>Download decisions</button>
        <label className="file-button">Open decisions<input ref={input} type="file" accept=".json,application/json"
          onChange={event => { void importer.open(event.target.files?.[0], bookmarkFocus(lastFocus.current)); event.target.value = ""; }} /></label>
        <button onClick={() => setHelp(!help)} aria-expanded={help}>Keyboard help</button></div>
      {help && <p>Focus a decision box: A match, R different, U unsure, S clear draft, J/K next/previous visible box, ? help. Text inputs keep their normal keys.</p>}
      <details><summary>Apply the decisions locally</summary>
        <p>Keep the original packet, catalogs, namespace and journal directory. Run the local apply command, then generate a fresh packet before reviewing changed or previously recorded decisions.</p>
        <code>python -m entity_matcher apply-rulings catalog-a.csv catalog-b.csv --packet review-packet.json --draft entity-matcher-decisions.json --journal-dir local-journal --namespace YOUR_NAMESPACE</code>
        <p>Skip or clearing an unsubmitted draft writes nothing. Reversing a recorded decision requires a retraction from a fresh packet. A conflict or uncertain write must be reconciled with the same operation IDs.</p>
      </details>
      {message && <p role="status">{message}</p>}{importer.busy && <p role="status">Opening decisions…</p>}
      {error && <p role="alert" className="error">{error}</p>}{importer.error && <p role="alert" className="error">{importer.error}</p>}
    </section>
    <ReviewPageView page={packet.page} renderActions={item => {
      const index = packet.entries.findIndex(entry => entry.key === item.key), entry = packet.entries[index];
      const operation = draft.operations.find(operation => operation.pair_key === item.key);
      const disabled = entry.issue !== null || packet.journalStatus !== "clean";
      const note = operation?.reason ?? "";
      return <fieldset className="draft-actions" id={`draft-actions-${index}`} tabIndex={0} disabled={disabled}
        onKeyDown={event => {
          if (event.target !== event.currentTarget || event.altKey || event.ctrlKey || event.metaKey) return;
          const key = event.key.toLowerCase();
          const shortcuts: Record<string, Verdict | "skip"> = { a: "positive", r: "negative", u: "unsure", s: "skip" };
          if (shortcuts[key]) { event.preventDefault(); choose(item.key, shortcuts[key], note); }
          else if (key === "j" || key === "k") { event.preventDefault(); document.getElementById(`draft-actions-${index + (key === "j" ? 1 : -1)}`)?.focus(); }
          else if (key === "?") { event.preventDefault(); setHelp(!help); }
        }}>
        <legend>Draft decision</legend>
        {disabled && <p>Decision unavailable: {entry.issue ?? packet.journalStatus}.</p>}
        <div className="draft-toolbar"><button aria-pressed={operation?.action === "decide" && operation.verdict === "positive"} onClick={() => choose(item.key, "positive", note)}>Match</button>
          <button aria-pressed={operation?.action === "decide" && operation.verdict === "negative"} onClick={() => choose(item.key, "negative", note)}>Different</button>
          <button aria-pressed={operation?.action === "decide" && operation.verdict === "unsure"} onClick={() => choose(item.key, "unsure", note)}>Unsure</button>
          <button onClick={() => choose(item.key, "skip")} disabled={!operation}>Clear draft / skip</button>
          <button disabled={entry.retractable === null} aria-pressed={operation?.action === "retract"} onClick={() => choose(item.key, "retract", note)}>Draft retraction</button></div>
        {operation && <><p>Draft: {operation.action === "decide" ? operation.verdict : "retract recorded decision"}.</p>
          <label>Reason (up to 4,096 UTF-8 bytes)<textarea value={note} maxLength={4096} rows={2}
            onChange={event => choose(item.key, operation.action === "decide" ? operation.verdict : "retract", event.target.value)} /></label></>}
        {entry.head && <details><summary>Reported observed journal head</summary><code>{entry.head}</code></details>}
      </fieldset>;
    }} />
    <ReplacementDialog id="replace-decisions" pending={importer.pending} fallback={input}
      onCancel={importer.cancel} onAccept={importer.accept} />
  </div>;
}
