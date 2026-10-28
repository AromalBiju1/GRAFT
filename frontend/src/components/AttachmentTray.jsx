import { fmtSize } from "../api";

const kind = (name = "", type = "") => {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if (ext === ".pdf" || type.includes("pdf")) return "PDF";
  if (ext === ".docx" || type.includes("word")) return "DOCX";
  return "TXT";
};

export default function AttachmentTray({ files, onRemove }) {
  if (!files.length) return null;
  return (
    <ul className="tray" aria-label="Attached files">
      {files.map((f) => (
        <li key={f.id} className={`chip ${f.state}`}>
          <span className="chip-icon">{kind(f.file?.name, f.file?.type)}</span>
          <span className="chip-meta">
            <strong title={f.file?.name}>{f.file?.name}</strong>
            <small>{fmtSize(f.file?.size || 0)}</small>
            {f.state === "uploading" && (
              <progress
                max="100"
                value={f.progress ?? 0}
                aria-label={`Uploading ${f.file?.name}`}
              />
            )}
            {f.state === "processing" && <small>Indexing document in ChromaDB…</small>}
            {f.state === "ready" && <small className="ready-label">Ready in ChromaDB</small>}
            {f.state === "failed" && <small className="err-label">{f.error || "Failed"}</small>}
          </span>
          {["queued", "ready", "failed"].includes(f.state) && (
            <button
              className="chip-x"
              onClick={() => onRemove(f.id)}
              aria-label={`Remove ${f.file?.name}`}
            >
              ×
            </button>
          )}
          {f.state === "uploading" && <span className="pct">{f.progress ?? 0}%</span>}
        </li>
      ))}
    </ul>
  );
}
