import { useState } from "react";

export default function QueryForm({ onSubmit, loading = false }) {
  const [query, setQuery] = useState("");

  function handleSubmit(event) {
    event.preventDefault();
    if (!loading && query.trim()) onSubmit(query.trim());
  }

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <label htmlFor="query">Your question</label>
      <textarea id="query" value={query} onChange={(event) => setQuery(event.target.value)}
        placeholder="What would you like to know about your documents?" rows={4} required disabled={loading} />
      <button type="submit" disabled={loading || !query.trim()}>{loading ? "Asking…" : "Ask GRAFT"}</button>
    </form>
  );
}
