import { useState } from "react";
import QueryForm from "./QueryForm.jsx";
import AnswerPanel from "./AnswerPanel.jsx";
import EvidenceList from "./EvidenceList.jsx";
import RoutingBadges from "./RoutingBadges.jsx";
import { submitQuery } from "./query.js";
import "./styles.css";

export default function App() {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleQuery(query) {
    if (loading || !query.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      setResult(await submitQuery(query.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <header>
        <h1>GRAFT</h1>
        <p>Ask a question and explore the supporting evidence.</p>
      </header>
      <QueryForm onSubmit={handleQuery} loading={loading} />
      {loading && <p className="loading" role="status"><span className="spinner" aria-hidden="true" />Retrieving evidence and generating an answer…</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {result && (
        <div className="results">
          <RoutingBadges routing={result.routing} />
          <AnswerPanel answer={result.answer} />
          <EvidenceList evidence={result.evidence} />
        </div>
      )}
    </main>
  );
}
