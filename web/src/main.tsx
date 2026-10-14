import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

function SetupPreview() {
  return (
    <main>
      <p className="eyebrow">Product catalog review</p>
      <h1>Entity Matcher</h1>
      <p className="intro">Compare catalogs with evidence you can inspect.</p>
      <section aria-labelledby="setup-heading">
        <span className="badge">Setup preview</span>
        <h2 id="setup-heading">The comparison workspace is being prepared.</h2>
        <p>Catalog import, matching and review are not available in this scaffold.</p>
        <ol>
          <li>Import two product catalogs.</li>
          <li>Inspect suggested matches and package sizes.</li>
          <li>Record decisions and export reviewed results.</li>
        </ol>
      </section>
    </main>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing page root");
createRoot(root).render(<StrictMode><SetupPreview /></StrictMode>);
