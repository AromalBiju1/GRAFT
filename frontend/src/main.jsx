import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';

function App() {
  const [query, setQuery] = useState('');
  const [answer, setAnswer] = useState('');
  const [evidence, setEvidence] = useState([]);
  const [routing, setRouting] = useState(null);
  const [loading, setLoading] = useState(false);

  async function ask(e) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setAnswer('');
    try {
      const res = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });
      // Fallback to direct API host when Vite proxy is not running
      const data = res.ok ? await res.json() : await (await fetch('http://127.0.0.1:8000/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })).json();
      setAnswer(data.answer ?? JSON.stringify(data));
      setEvidence(data.evidence ?? []);
      setRouting(data.routing ?? null);
    } catch (err) {
      setAnswer(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 860, margin: '2rem auto', fontFamily: 'system-ui, sans-serif', padding: '0 1rem' }}>
      <h1>GRAFT</h1>
      <p style={{ color: '#666' }}>Gated Retrieval Activation Framework for Trees — query the hierarchical index.</p>
      <form onSubmit={ask} style={{ display: 'flex', gap: 8 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask a question about your documents..."
          style={{ flex: 1, padding: '0.6rem 0.8rem', fontSize: 16 }}
        />
        <button disabled={loading} style={{ padding: '0.6rem 1.2rem' }}>{loading ? '...' : 'Ask'}</button>
      </form>
      {routing && (
        <p style={{ fontSize: 13, color: '#444', marginTop: 8 }}>
          Routing: <code>{routing.complexity}</code> (conf {routing.confidence}) · depth {routing.retrieval_depth} · modules: {routing.activated_modules.join(', ')}
        </p>
      )}
      {answer && (
        <div style={{ marginTop: 20, background: '#f7f7f7', padding: 16, borderRadius: 8, whiteSpace: 'pre-wrap' }}>{answer}</div>
      )}
      {evidence.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h3>Evidence</h3>
          <ul>
            {evidence.map((ev, i) => (
              <li key={i} style={{ marginBottom: 8, fontSize: 14 }}>
                <strong>{ev.source ?? ev.chunk_id ?? `passage ${i+1}`}</strong>
                {ev.score != null && <> — score {Number(ev.score).toFixed(3)}</>}
                {ev.text && <div style={{ color: '#333', marginTop: 4 }}>{ev.text.slice(0, 300)}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
