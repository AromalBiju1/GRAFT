import { useRef, useEffect } from "react";
import { ACCEPT, MAX_MB } from "../api";

export default function InputBar({
  value,
  onChange,
  onSend,
  onStop,
  onPick,
  busy,
  canSend,
  scope,
  onScopeChange,
  hasFiles,
}) {
  const ta = useRef(null);
  const picker = useRef(null);

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, [value]);

  const key = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend && !busy) onSend();
    }
  };

  return (
    <div className="research-input-container">
      {/* Scope Selector Bar */}
      <div className="scope-selector-row">
        <span className="scope-label">Retrieval Scope:</span>
        <div className="scope-pills" role="radiogroup" aria-label="Retrieval Scope">
          <button
            type="button"
            className={`scope-pill-btn ${scope === "all" ? "active" : ""}`}
            onClick={() => onScopeChange("all")}
            title="Search the entire pre-indexed GRAFT ChromaDB corpus"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="2" y1="12" x2="22" y2="12"></line>
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
            </svg>
            Entire Knowledge Base
          </button>
          <button
            type="button"
            className={`scope-pill-btn ${scope === "files" ? "active" : ""}`}
            onClick={() => onScopeChange("files")}
            disabled={!hasFiles}
            title={hasFiles ? "Filter retrieval strictly to uploaded session files" : "Attach a document first to enable this mode"}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
            </svg>
            Attached Documents Only {!hasFiles && "(attach a file)"}
          </button>
        </div>
      </div>

      {/* Main Input Bar */}
      <div className="inputbar">
        <input
          ref={picker}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            onPick([...e.target.files]);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className="icon-btn attach-btn"
          onClick={() => picker.current.click()}
          aria-label="Attach documents"
          title={`Upload PDF, DOCX or TXT · up to ${MAX_MB} MB`}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
          </svg>
        </button>

        <textarea
          ref={ta}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={key}
          placeholder={
            scope === "all"
              ? "Ask a question across the entire GRAFT corpus (e.g. 'Compare findings on...', 'What are the contradictions in...')"
              : "Ask a question about your uploaded documents..."
          }
          aria-label="Research Prompt"
        />

        {busy ? (
          <button
            type="button"
            className="send stop"
            onClick={onStop}
            aria-label="Stop research synthesis"
            title="Stop generation"
          >
            ■
          </button>
        ) : (
          <button
            type="button"
            className="send"
            onClick={onSend}
            disabled={!canSend}
            aria-label="Send query"
            title="Execute query"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        )}
      </div>
      <div className="input-hint">
        <span>Enter ↵ to synthesize • Shift+Enter for new line • Adaptive Tree Retrieval Active</span>
      </div>
    </div>
  );
}
