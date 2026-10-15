import { useRef, useState } from "react";
import { MAX_REPORT_BYTES, parseInspection, type Inspection } from "./inspection";

export function App() {
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const request = useRef(0);
  async function open(file: File | undefined) {
    const ticket = ++request.current;
    setInspection(null); setError(""); setPage(0); setFilter("");
    setBusy(Boolean(file));
    if (!file) return;
    try {
      if (file.size > MAX_REPORT_BYTES) throw new Error("Choose a report smaller than 20 MiB.");
      const result = parseInspection(new Uint8Array(await file.arrayBuffer()));
      if (ticket === request.current) setInspection(result);
    } catch (error) {
      if (ticket === request.current) setError(error instanceof Error ? error.message : "Could not open the report.");
    } finally { if (ticket === request.current) setBusy(false); }
  }
  const matches = inspection?.candidates.filter(candidate =>
    candidate.refs.some(ref => `${ref.dataset_id} ${ref.record_id}`.toLowerCase().includes(filter.toLowerCase()))) ?? [];
  const pages = Math.max(1, Math.ceil(matches.length / 50));
  const current = Math.min(page, pages - 1);
  return <main>
    <header><p className="eyebrow">Product catalog review</p><h1>Entity Matcher</h1>
      <p className="intro">Inspect the candidate pairs from your local comparison.</p></header>
    <section aria-labelledby="open-heading" className="open-panel">
      <div><span className="badge">Candidate preview</span><h2 id="open-heading">Open a comparison report</h2>
        <p>Choose the JSON output from the local candidates command. The report stays in this page.</p></div>
      <label className="file-button">Choose report<input type="file" accept=".json,application/json"
        onChange={event => { void open(event.target.files?.[0]); event.target.value = ""; }} /></label>
      <p className="muted">Candidates are suggestions for inspection. This view records no decisions.</p>
    </section>
    {busy && <p role="status">Opening report…</p>}
    {error && <p className="error" role="alert">{error}</p>}
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
  </main>;
}
