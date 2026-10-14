import { fmtSize } from "../api";

/** Choose a PDF, DOCX, or fallback TXT label from the attachment's MIME type. */
const kind = (t) => (t.includes("pdf") ? "PDF" : t.includes("word") ? "DOCX" : "TXT");

/** Show attachment metadata, upload progress, processing state, and queued-file removal. */
export default function AttachmentTray({ files, progress, onRemove }) {
  if (!files.length) return null;
  return (
    <ul className="tray" aria-label="Attached files">
      {files.map((f) => (
        <li key={f.id} className={`chip ${f.state}`}>
          <span className="chip-icon">{kind(f.file.type)}</span>
          <span className="chip-meta">
            <strong title={f.file.name}>{f.file.name}</strong>
            <small>{fmtSize(f.file.size)} · {f.file.type || "unknown"}</small>
            {f.state === "uploading" && (
              <progress max="100" value={progress} aria-label={`Uploading ${f.file.name}`} />
            )}
            {f.state === "processing" && <small>Reading document…</small>}
            {f.state === "ready" && <small>Ready</small>}
          </span>
          {f.state === "queued" && (
            <button className="chip-x" onClick={() => onRemove(f.id)} aria-label={`Remove ${f.file.name}`}>×</button>
          )}
          {f.state === "uploading" && <span className="pct">{progress}%</span>}
        </li>
      ))}
    </ul>
  );
}
