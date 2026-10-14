/** Render the sidebar toggle, chat history, new-chat action, and active session's files. */
export default function Sidebar({ open, onToggle, sessions, activeId, onSelect, onNew, files }) {
  return (
    <aside className={`sidebar ${open ? "open" : ""}`}>
      <button className="icon-btn" onClick={onToggle} aria-label={open ? "Collapse sidebar" : "Open sidebar"}>☰</button>
      {open && (
        <>
          <button className="new-chat" onClick={onNew}>+ New chat</button>
          <h2>History</h2>
          <ul>{sessions.map((s) => (
            <li key={s.id}><button className={s.id === activeId ? "active" : ""} onClick={() => onSelect(s.id)}>{s.title}</button></li>
          ))}</ul>
          <h2>Files in this chat</h2>
          <ul>{files.length ? files.map((n) => <li key={n} className="file">{n}</li>) : <li className="muted">No files yet</li>}</ul>
        </>
      )}
    </aside>
  );
}
