import { useState, useRef, useCallback } from "react";
import Sidebar from "./components/Sidebar";
import Feed from "./components/Feed";
import InputBar from "./components/InputBar";
import AttachmentTray from "./components/AttachmentTray";
import { sendChat, uploadFile, pollFileStatus, validate } from "./api";
import "./chat.css";

const GREETING = {
  id: "g",
  role: "assistant",
  text: "Hi! Ask me anything about the GRAFT corpus, or upload documents to query them specifically.",
};

const newSession = () => ({
  id: crypto.randomUUID(),
  title: "New chat",
  messages: [GREETING],
  files: [],
  fileIds: [],
});

export default function App() {
  const [sessions, setSessions] = useState([newSession()]);
  const [activeId, setActiveId] = useState(sessions[0].id);
  const [sidebar, setSidebar] = useState(true);
  const [text, setText] = useState("");
  const [queue, setQueue] = useState([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [drag, setDrag] = useState(false);
  const cancel = useRef(null);
  const depth = useRef(0);

  const active = sessions.find((s) => s.id === activeId) || sessions[0];
  const patch = useCallback(
    (fn) =>
      setSessions((all) =>
        all.map((s) => (s.id === activeId ? fn(s) : s))
      ),
    [activeId]
  );

  const addFiles = async (list) => {
    const { accepted, errors } = validate(list, queue);
    if (errors.length) {
      setNotice(errors.join(" "));
    }
    if (!accepted.length) return;

    // Start each accepted file in uploading state
    const startingQueueItems = accepted.map((item) => ({
      ...item,
      state: "uploading",
      progress: 0,
      fid: null,
      error: null,
    }));

    setQueue((q) => [...q, ...startingQueueItems]);

    for (const item of startingQueueItems) {
      try {
        const uploaded = await uploadFile(item.file, (pct) => {
          setQueue((q) =>
            q.map((f) => (f.id === item.id ? { ...f, progress: pct } : f))
          );
        });

        setQueue((q) =>
          q.map((f) =>
            f.id === item.id
              ? { ...f, state: "processing", fid: uploaded.id, progress: 100 }
              : f
          )
        );

        patch((s) => ({
          ...s,
          files: Array.from(new Set([...s.files, uploaded.name])),
        }));

        await pollFileStatus(uploaded.id);

        setQueue((q) =>
          q.map((f) =>
            f.id === item.id ? { ...f, state: "ready" } : f
          )
        );

        patch((s) => ({
          ...s,
          fileIds: Array.from(new Set([...(s.fileIds || []), uploaded.id])),
        }));
      } catch (err) {
        setQueue((q) =>
          q.map((f) =>
            f.id === item.id
              ? { ...f, state: "failed", error: err.message }
              : f
          )
        );
        setNotice(`Failed to process ${item.file.name}: ${err.message}`);
      }
    }
  };

  const send = () => {
    const prompt = text.trim();
    if (!prompt && !queue.length) return;

    const aid = crypto.randomUUID();
    const readyFids = queue
      .filter((f) => f.state === "ready" && f.fid)
      .map((f) => f.fid);
    const sessionFids = active.fileIds || [];
    const allFileIds = Array.from(new Set([...sessionFids, ...readyFids]));

    const activeFilesList = queue.map((f) => f.file.name);

    patch((s) => ({
      ...s,
      title:
        s.title === "New chat"
          ? (prompt || activeFilesList[0] || "New chat").slice(0, 32)
          : s.title,
      messages: [
        ...s.messages,
        {
          id: crypto.randomUUID(),
          role: "user",
          text: prompt,
          files: activeFilesList,
        },
        {
          id: aid,
          role: "assistant",
          text: "",
          routing: null,
          evidence: [],
        },
      ],
    }));

    const setAssistant = (fn) =>
      patch((s) => ({
        ...s,
        messages: s.messages.map((m) => (m.id === aid ? fn(m) : m)),
      }));

    setText("");
    setNotice("");
    setBusy(true);

    cancel.current = sendChat({
      prompt,
      fileIds: allFileIds,
      onEvent: (ev, d) => {
        if (ev === "token") {
          setAssistant((m) => ({
            ...m,
            text: m.text + (d.text || d.t || ""),
          }));
        } else if (ev === "meta") {
          setAssistant((m) => ({
            ...m,
            routing: d.routing || m.routing,
            evidence: d.evidence || m.evidence,
          }));
        } else if (ev === "status") {
          setQueue((q) =>
            q.map((f) =>
              f.fid === d.file ? { ...f, state: d.state || d.status } : f
            )
          );
        } else if (ev === "error") {
          setAssistant((m) => ({
            ...m,
            text: d.message || "An error occurred",
            error: true,
          }));
        }
      },
      onError: (msg) => {
        setAssistant((m) => ({ ...m, text: msg, error: true }));
        setNotice(msg);
      },
      onDone: () => {
        setBusy(false);
        cancel.current = null;
      },
    });
  };

  const dropProps = {
    onDragEnter: (e) => {
      e.preventDefault();
      depth.current++;
      setDrag(true);
    },
    onDragOver: (e) => e.preventDefault(),
    onDragLeave: () => {
      if (--depth.current <= 0) {
        depth.current = 0;
        setDrag(false);
      }
    },
    onDrop: (e) => {
      e.preventDefault();
      depth.current = 0;
      setDrag(false);
      addFiles([...e.dataTransfer.files]);
    },
  };

  return (
    <div className="app">
      <Sidebar
        open={sidebar}
        onToggle={() => setSidebar((o) => !o)}
        sessions={sessions}
        activeId={activeId}
        onSelect={setActiveId}
        files={active.files}
        onNew={() => {
          const s = newSession();
          setSessions((a) => [s, ...a]);
          setActiveId(s.id);
          setQueue([]);
        }}
      />
      <main className="chat" {...dropProps}>
        <header>
          <img src="/graft-logo.png" alt="" width="40" height="40" />
          <div>
            <h1>GRAFT</h1>
            <p>Adaptive, Complexity-Aware Retrieval-Augmented Generation</p>
          </div>
        </header>
        <Feed messages={active.messages} busy={busy} />
        {drag && (
          <div className="dropzone">Drop PDF, DOCX or TXT files to attach</div>
        )}
        <footer>
          {notice && (
            <div className="notice" role="alert">
              {notice}
            </div>
          )}
          <AttachmentTray
            files={queue}
            onRemove={(id) => {
              const fileToRemove = queue.find((f) => f.id === id);
              setQueue((q) => q.filter((f) => f.id !== id));
              if (fileToRemove?.fid) {
                patch((s) => ({
                  ...s,
                  fileIds: (s.fileIds || []).filter((fid) => fid !== fileToRemove.fid),
                }));
              }
            }}
          />
          <InputBar
            value={text}
            onChange={setText}
            onSend={send}
            onStop={() => cancel.current?.()}
            onPick={addFiles}
            busy={busy}
            canSend={!!text.trim()}
          />
        </footer>
      </main>
    </div>
  );
}
