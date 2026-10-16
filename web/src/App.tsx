import { useRef, useState } from "react";
import { object, parseInspection, readReportJSON, type Inspection } from "./inspection";
import { parseReviewPage, type ReviewPage } from "./reviewPage";
import { ReviewPageView } from "./ReviewPageView";
import { hasRulingPacketTag, parseRulingPacket, type RulingPacket } from "./rulingPacket";
import { RulingPacketView } from "./RulingPacketView";
import { ReplacementDialog } from "./ReplacementDialog";
import { bookmarkFocus, isDraftFocus, useValidatedImport } from "./useValidatedImport";
import { LocalComparisonForm } from "./LocalComparisonForm";
import { compareLocally } from "./localComparison";

type ImportedReport = { kind: "packet"; value: RulingPacket } | { kind: "review"; value: ReviewPage } | { kind: "inspection"; value: Inspection };

export function App() {
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [reviewPage, setReviewPage] = useState<ReviewPage | null>(null);
  const [rulingPacket, setRulingPacket] = useState<RulingPacket | null>(null);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const [version, setVersion] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  const draftPresent = useRef(false);
  const importer = useValidatedImport<ImportedReport>({
    parse(bytes) {
      if (hasRulingPacketTag(bytes)) return { kind: "packet", value: parseRulingPacket(bytes) };
      if (object(readReportJSON(bytes)).kind === "product_review_page") return { kind: "review", value: parseReviewPage(bytes) };
      return { kind: "inspection", value: parseInspection(bytes) };
    },
    hasDraft: () => draftPresent.current,
    commit(report) {
      draftPresent.current = false;
      setInspection(report.kind === "inspection" ? report.value : null);
      setReviewPage(report.kind === "review" ? report.value : null);
      setRulingPacket(report.kind === "packet" ? report.value : null);
      setVersion(value => value + 1); setPage(0); setFilter("");
    },
  });
  const matches = inspection?.candidates.filter(candidate =>
    candidate.refs.some(ref => `${ref.dataset_id} ${ref.record_id}`.toLowerCase().includes(filter.toLowerCase()))) ?? [];
  const pages = Math.max(1, Math.ceil(matches.length / 50));
  const current = Math.min(page, pages - 1);
  return <><main onFocusCapture={event => { if (isDraftFocus(event.target) && !event.target.closest("[data-local-comparison]")) lastFocus.current = event.target; }}>
    <header><p className="eyebrow">Product catalog review</p><h1>Entity Matcher</h1>
      <p className="intro">Inspect the candidate pairs from your local comparison.</p></header>
    <section aria-labelledby="open-heading" className="open-panel">
      <div><span className="badge">Candidate preview</span><h2 id="open-heading">Open a comparison report</h2>
        <p>Choose a candidate report, review page or ruling packet. Its contents stay in this page.</p></div>
      <label className="file-button">Choose report<input ref={input} type="file" accept=".json,application/json"
        onChange={event => { void importer.open(event.target.files?.[0], bookmarkFocus(lastFocus.current)); event.target.value = ""; }} /></label>
      <p className="muted">Candidates are suggestions. Ruling packets support local decision drafts for download; this page cannot write your journal.</p>
    </section>
    <LocalComparisonForm busy={importer.busy} onEdit={importer.invalidate} onCompare={request => {
      void importer.openRequest(signal => compareLocally(request, { origin: window.location.origin, signal }), bookmarkFocus(lastFocus.current));
    }} />
    {importer.busy && <p role="status">Opening report…</p>}
    {importer.error && <p className="error" role="alert">{importer.error}</p>}
    {reviewPage && <ReviewPageView key={version} page={reviewPage} />}
    {rulingPacket && <RulingPacketView key={version} packet={rulingPacket} onDraftPresentChange={present => { draftPresent.current = present; importer.invalidate(); }} />}
    {inspection && <>
      <section aria-labelledby="summary-heading"><h2 id="summary-heading">Imported report</h2>
        <p className="muted">Counts and identifiers are reported metadata. Catalog contents and current source revisions have not been checked here.</p>
        <div className="stats">{inspection.catalogs.map(c => <div key={c.dataset}>
          <span className="stat">{c.count.toLocaleString()}</span><span className="identity">{c.dataset}</span><small>catalog records</small></div>)}
          <div><span className="stat">{inspection.retained.toLocaleString()}</span><small>retained candidates</small></div>
          <div><span className="stat">{inspection.possible.toLocaleString()}</span><small>possible pairs</small></div></div>
        <p>Original union: {inspection.exact ? inspection.lower.toLocaleString() : `${inspection.lower.toLocaleString()}–${inspection.upper.toLocaleString()} (bounds)`}.</p>
        <p>Per-record cap omissions: {inspection.perADrops.toLocaleString()}. Global cap omissions: {inspection.globalDrops.toLocaleString()}.</p>
        {(inspection.omissions > 0 || inspection.tokenTruncations > 0) && <p className="notice">Blocking restrictions were reported: {inspection.omissions} posting omissions and {inspection.tokenTruncations} records with truncated token keys.</p>}
        {inspection.truncated && <p className="notice">The file contains {inspection.candidates.length} of {inspection.retained.toLocaleString()} retained pairs. Search covers this preview.</p>}
      </section>
      <section aria-labelledby="pairs-heading"><div className="toolbar"><h2 id="pairs-heading">Candidate pairs</h2>
        <label>Find a record<input type="search" value={filter} maxLength={256}
          onChange={event => { setFilter(event.target.value); setPage(0); }} placeholder="Record or catalog ID" /></label></div>
        <p className="muted">{matches.length} preview pairs shown in pages of 50. Blocking overlap is not a match probability.</p>
        {matches.slice(current * 50, (current + 1) * 50).map(candidate => <article className="candidate" key={candidate.pair_key}>
          <div className="pair">{candidate.refs.map(ref => <div key={ref.dataset_id}>
            <span className="catalog">{ref.dataset_id}</span><h3 className="identity">{ref.record_id}</h3>
            <details><summary>Reported raw fingerprint</summary><code>{ref.content_hash}</code></details></div>)}</div>
          <p className="muted">{candidate.blockers.join(" + ")} · {candidate.blocking_overlap} blocking overlaps</p>
          <details><summary>Reported pair identity</summary><code>{candidate.pair_key}</code><code>{candidate.pair_generation}</code></details>
        </article>)}
        {matches.length === 0 && <p>No candidate pairs match this search.</p>}
        <nav aria-label="Candidate pages"><button disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button>
          <span>Page {current + 1} of {pages}</span><button disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>Next</button></nav>
      </section>
    </>}
  </main><ReplacementDialog id="replace-report" pending={importer.pending} fallback={input}
    onCancel={importer.cancel} onAccept={importer.accept} /></>;
}
