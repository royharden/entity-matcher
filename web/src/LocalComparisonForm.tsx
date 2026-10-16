import { useState } from "react";
import { localViewerAvailable, type LocalComparison } from "./localComparison";

export function LocalComparisonForm({ busy, onCompare, onEdit }: {
  busy: boolean; onCompare: (input: LocalComparison) => void; onEdit: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [port, setPort] = useState("");
  const [token, setToken] = useState("");
  const [a, setA] = useState<File | null>(null), [b, setB] = useState<File | null>(null);
  const available = localViewerAvailable(window.location.origin);
  return <section data-local-comparison aria-labelledby="local-comparison-heading">
    <span className="badge">Local CSV comparison</span><h2 id="local-comparison-heading">Compare two catalogs</h2>
    <p>Send two CSV files to your local comparison service and inspect its first page of candidates. Your original files stay unchanged.</p>
    {!available && <p className="notice">Local connections are available when this viewer is served from HTTP localhost. Hosted copies can open exported reports above.</p>}
    <button type="button" aria-expanded={open} aria-controls="local-comparison-fields" disabled={!available}
      onClick={() => { onEdit(); setOpen(!open); if (open) { setToken(""); setPort(""); setA(null); setB(null); } }}>
      {open ? "Close local connection" : "Connect local service"}</button>
    {open && <form id="local-comparison-fields" className="local-comparison-form" noValidate
      onSubmit={event => { event.preventDefault(); onCompare({ port, token, a, b }); }}>
      <p className="muted">Start the Python comparison service and allow this page's port as a frontend origin. Connection details are not saved; closing this panel clears its fields.</p>
      <div className="local-comparison-fields">
        <label>Local service port<input type="text" inputMode="numeric" maxLength={5} autoComplete="off" value={port}
          onChange={event => { onEdit(); setPort(event.target.value); }} /></label>
        <label>Review token<input type="password" maxLength={128} autoComplete="off" value={token}
          onChange={event => { onEdit(); setToken(event.target.value); }} /></label>
        <label>Catalog A (CSV)<input type="file" accept=".csv,text/csv" onChange={event => { onEdit(); setA(event.target.files?.[0] ?? null); }} /></label>
        <label>Catalog B (CSV)<input type="file" accept=".csv,text/csv" onChange={event => { onEdit(); setB(event.target.files?.[0] ?? null); }} /></label>
      </div>
      <p className="muted">The combined encoded upload is limited to 20 MiB. This preview is read-only; open a ruling packet to draft decisions.</p>
      <div className="draft-toolbar"><button type="submit" disabled={busy}>{busy ? "Comparing catalogs…" : "Compare catalogs"}</button>
        {busy && <button type="button" onClick={onEdit}>Cancel loading</button>}</div>
    </form>}
  </section>;
}
