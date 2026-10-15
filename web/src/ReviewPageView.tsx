import { useState } from "react";
import { RAW_FIELDS, type ReviewPage } from "./reviewPage";

const percent = (value: number | null) => value === null ? "Unavailable" : `${(value * 100).toFixed(1)}%`;
const score = (value: number | null) => value === null ? "Unavailable" : value.toFixed(3);
export function ReviewPageView({ page }: { page: ReviewPage }) {
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const items = page.items.filter(item => item.records.some(record =>
    `${record.ref.dataset_id} ${record.raw.id} ${record.raw.name} ${record.raw.brand}`.toLowerCase().includes(filter.toLowerCase())));
  const pages = Math.max(1, Math.ceil(items.length / 50));
  const current = Math.min(offset, pages - 1);
  return <>
    <section aria-labelledby="review-summary"><span className="badge">Imported review page</span>
      <h2 id="review-summary">Raw records and machine proposals</h2>
      <p className="notice">This imported file has not been checked against current catalogs. Scores, calibration and evidence statuses are reported metadata. It grants no human decision or journal authority.</p>
      <div className="stats">{page.catalogs.map(c => <div key={c.dataset}><span className="stat">{c.count.toLocaleString()}</span><span className="identity">{c.dataset}</span><small>catalog records</small></div>)}
        <div><span className="stat">{page.retained.toLocaleString()}</span><small>retained pairs</small></div>
        <div><span className="stat">{page.unsupported}</span><small>unsupported records on this page</small></div></div>
      <p>{page.items.length} pairs in this file, starting at offset {page.offset}. {page.truncated && "More retained pairs exist outside this file."}</p>
      <p className="muted">Original union: {page.exact ? page.lower.toLocaleString() : `${page.lower.toLocaleString()}–${page.upper.toLocaleString()} (bounds)`}. Per-record cap omissions: {page.perADrops.toLocaleString()}; global cap omissions: {page.globalDrops.toLocaleString()}; posting omissions: {page.omissions}; token truncations: {page.tokenTruncations}.</p>
      <details><summary>Reported source fingerprints</summary>{page.catalogs.map(c => <p key={c.dataset}><span className="identity">{c.dataset}</span><code>{c.sha256}</code></p>)}</details>
    </section>
    <section aria-labelledby="review-pairs"><div className="toolbar"><h2 id="review-pairs">Inspect pairs</h2>
      <label>Find a product<input type="search" maxLength={256} value={filter} placeholder="Name, brand or record ID"
        onChange={event => { setFilter(event.target.value); setOffset(0); }} /></label></div>
      {items.slice(current * 50, (current + 1) * 50).map(item => <article className="candidate" key={item.key}>
        <div className="proposal-heading"><span className="badge">Machine proposal: {item.outcome}</span><span>{item.method} · {item.status}</span></div>
        <div className="pair raw-pair">{item.records.map(record => <div key={record.ref.dataset_id}>
          <p className="catalog identity">{record.ref.dataset_id}</p><h3 className="identity">{record.raw.name}</h3>
          <dl>{RAW_FIELDS.map(field => <div className="raw-field" key={field}><dt>{field}</dt><dd>{record.raw[field] || <span className="muted">Empty</span>}</dd></div>)}</dl>
          <details><summary>Reported raw fingerprint</summary><code>{record.ref.content_hash}</code></details>
        </div>)}</div>
        <div className="score-grid"><p>Raw relatedness<strong>{score(item.relatedness)}</strong></p>
          <p>Native confidence<strong>{score(item.nativeConfidence)}</strong></p>
          <p>Reported calibrated probability<strong>{percent(item.pSame)}</strong></p></div>
        <p className="muted">Raw relatedness and native confidence are not correctness probabilities. Imported calibration is unverified.</p>
        {item.issues.length > 0 && <details open><summary>Reported review issues ({item.issues.length})</summary><ul>{item.issues.map(issue => <li key={issue}><code>{issue}</code></li>)}</ul></details>}
        <details><summary>Reported raw-field evidence ({item.evidence.length})</summary>
          {item.evidence.map(check => <div className="evidence-row" key={JSON.stringify([check.record.dataset_id, check.record.record_id, check.field])}>
            <span className="identity">{check.record.dataset_id} · {check.field} · {check.status}</span>
            {check.quote !== null && <blockquote>{check.quote}</blockquote>}
            {check.issue && <code>{check.issue}</code>}
          </div>)}
        </details>
        <details><summary>Reported pair identity</summary><code>{item.key}</code><code>{item.generation}</code></details>
      </article>)}
      {items.length === 0 && <p>No pairs match this search.</p>}
      <nav aria-label="Imported review page navigation"><button disabled={current === 0} onClick={() => setOffset(current - 1)}>Previous</button>
        <span>View {current + 1} of {pages} in this file</span><button disabled={current + 1 >= pages} onClick={() => setOffset(current + 1)}>Next</button></nav>
    </section>
  </>;
}
