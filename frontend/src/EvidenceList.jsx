export default function EvidenceList({ evidence }) {
  const items = Array.isArray(evidence) ? evidence.filter((item) => item && typeof item === "object") : [];

  return (
    <section className="panel" aria-labelledby="evidence-heading">
      <h2 id="evidence-heading">Evidence</h2>
      {items.length === 0 ? <p className="muted">No supporting evidence was returned.</p> : (
        <ol className="evidence-list">
          {items.map((item, index) => (
            <li key={`${item.node_id ?? item.chunk_id ?? "evidence"}-${index}`}>
              <dl className="evidence-meta">
                {typeof item.source === "string" && item.source && <div><dt>Source</dt><dd>{item.source}</dd></div>}
                {Number.isFinite(item.page) && <div><dt>Page</dt><dd>{item.page}</dd></div>}
                {Number.isFinite(item.level) && <div><dt>Level</dt><dd>{item.level}</dd></div>}
                {Number.isFinite(item.score) && <div><dt>Score</dt><dd>{item.score.toFixed(3)}</dd></div>}
              </dl>
              {typeof item.text === "string" && item.text && <p className="evidence-text">{item.text}</p>}
              {!item.source && !item.text && <p className="muted">Evidence item {index + 1}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
