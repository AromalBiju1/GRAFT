import { useState, useRef, useCallback } from "react";
import Sidebar from "./components/Sidebar";
import Feed from "./components/Feed";
import InputBar from "./components/InputBar";
import AttachmentTray from "./components/AttachmentTray";
import { sendChat, validate } from "./api";
import "./chat.css";

const GREETING = { id: "g", role: "assistant", text: "Hi! Upload a document, then ask me anything about it." };
/** Create a chat session with a unique ID, greeting, and empty file list. */
const newSession = () => ({ id: crypto.randomUUID(), title: "New chat", messages: [GREETING], files: [] });

/** Render chat sessions and manage attachments, upload progress, and streamed replies. */
export default function App() {
  const [sessions, setSessions] = useState([newSession()]);
  const [activeId, setActiveId] = useState(sessions[0].id);
  const [sidebar, setSidebar] = useState(true);
  const [text, setText] = useState("");
  const [queue, setQueue] = useState([]);
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [drag, setDrag] = useState(false);
  const cancel = useRef(null), depth = useRef(0);

  const active = sessions.find((s) => s.id === activeId);
  /** Apply a session transformation to the active conversation. */
  const patch = useCallback((fn) => setSessions((all) => all.map((s) => (s.id === activeId ? fn(s) : s))), [activeId]);

  /** Queue accepted files and display any validation errors. */
  const addFiles = (list) => {
    const { accepted, errors } = validate(list, queue);
    setQueue((q) => [...q, ...accepted]);
    setNotice(errors.join(" "));
  };

  /** Add messages, send the prompt and attachments, and update the streamed reply. */
  const send = () => {
    const prompt = text.trim(), files = queue;
    const aid = crypto.randomUUID();
    patch((s) => ({
      ...s, title: s.title === "New chat" ? (prompt || files[0]?.file.name || "New chat").slice(0, 32) : s.title,
      files: [...s.files, ...files.map((f) => f.file.name)],
      messages: [...s.messages, { id: crypto.randomUUID(), role: "user", text: prompt, files: files.map((f) => f.file.name) },
                 { id: aid, role: "assistant", text: "" }],
    }));
    /** Apply a transformation to this request's assistant message. */
    const setAssistant = (fn) => patch((s) => ({ ...s, messages: s.messages.map((m) => (m.id === aid ? fn(m) : m)) }));
    /** Update the processing state of every queued attachment. */
    const setFiles = (state) => setQueue((q) => q.map((f) => ({ ...f, state })));
    setText(""); setNotice(""); setBusy(true); setProgress(0); setFiles("uploading");

    cancel.current = sendChat({
      prompt, files,
      onUpload: (p) => { setProgress(p); if (p === 100) setFiles("processing"); },
      onEvent: (ev, d) => {
        if (ev === "token") setAssistant((m) => ({ ...m, text: m.text + d.text }));
        if (ev === "status") setQueue((q) => q.map((f) => (d.file.endsWith(f.file.name.replace(/[^A-Za-z0-9._-]/g, "_")) ? { ...f, state: d.state } : f)));
        if (ev === "error") setAssistant((m) => ({ ...m, text: d.message, error: true }));
      },
      onError: (msg) => { setAssistant((m) => ({ ...m, text: msg, error: true })); setNotice(msg); },
      onDone: () => { setBusy(false); setQueue([]); cancel.current = null; },
    });
  };

  const dropProps = {
    onDragEnter: (e) => { e.preventDefault(); depth.current++; setDrag(true); },
    onDragOver: (e) => e.preventDefault(),
    onDragLeave: () => { if (--depth.current <= 0) { depth.current = 0; setDrag(false); } },
    onDrop: (e) => { e.preventDefault(); depth.current = 0; setDrag(false); addFiles([...e.dataTransfer.files]); },
  };

  return (
    <div className="app">
      <Sidebar open={sidebar} onToggle={() => setSidebar((o) => !o)} sessions={sessions} activeId={activeId}
               onSelect={setActiveId} files={active.files}
               onNew={() => { const s = newSession(); setSessions((a) => [s, ...a]); setActiveId(s.id); setQueue([]); }} />
      <main className="chat" {...dropProps}>
        <header>
          <img src="/graft-logo.png" alt="" width="40" height="40" />
          <div><h1>GRAFT</h1><p>An Adaptive, Complexity-Aware Retrieval-Augmented Generation System</p></div>
        </header>
        <Feed messages={active.messages} busy={busy} />
        {drag && <div className="dropzone">Drop PDF, DOCX or TXT files to attach</div>}
        <footer>
          {notice && <div className="notice" role="alert">{notice}</div>}
          <AttachmentTray files={queue} progress={progress} onRemove={(id) => setQueue((q) => q.filter((f) => f.id !== id))} />
          <InputBar value={text} onChange={setText} onSend={send} onStop={() => cancel.current?.()} onPick={addFiles}
                    busy={busy} canSend={!!text.trim() || queue.length > 0} />
        </footer>
      </main>
    </div>
  );
}
