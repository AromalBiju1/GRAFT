import { useRef, useEffect } from "react";
import { ACCEPT, MAX_MB } from "../api";

export default function InputBar({ value, onChange, onSend, onStop, onPick, busy, canSend }) {
  const ta = useRef(null), picker = useRef(null);

  useEffect(() => {            // auto-expanding textarea (max ~8 lines)
    const el = ta.current; if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [value]);

  const key = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (canSend && !busy) onSend(); }
  };

  return (
    <div className="inputbar">
      <input ref={picker} type="file" multiple accept={ACCEPT} hidden
             onChange={(e) => { onPick([...e.target.files]); e.target.value = ""; }} />
      <button className="icon-btn" onClick={() => picker.current.click()} aria-label="Attach files"
              title={`PDF, DOCX or TXT · up to ${MAX_MB} MB`}>📎</button>
      <textarea ref={ta} rows={1} value={value} onChange={(e) => onChange(e.target.value)} onKeyDown={key}
                placeholder="Ask a question about your document..." aria-label="Message" />
      {busy
        ? <button className="send stop" onClick={onStop} aria-label="Stop generating">■</button>
        : <button className="send" onClick={onSend} disabled={!canSend} aria-label="Send">↗</button>}
    </div>
  );
}