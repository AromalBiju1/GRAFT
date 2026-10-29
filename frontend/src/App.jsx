import { useState, useRef, useCallback, useMemo } from "react";
import Sidebar from "./components/Sidebar";
import Feed from "./components/Feed";
import InputBar from "./components/InputBar";
import AttachmentTray from "./components/AttachmentTray";
import ResearchInspector from "./components/ResearchInspector";
import { sendChat, uploadFile, pollFileStatus, validate } from "./api";
import "./chat.css";
import "./research.css";

const GREETING = {
  id: "greeting",
  role: "assistant",
  text: "Welcome to the GRAFT Research Studio. Enter a research hypothesis or query across the knowledge base, or attach documents to explore hierarchical RAPTOR tree retrieval and reasoning modules.",
  routing: {
    complexity: "simple",
    confidence: 1.0,
    retrieval_depth: 0,
    activated_modules: ["fact_lookup"],
  },
  evidence: [],
  module_results: [],
  latency_ms: null,
};

const newSession = () => ({
  id: crypto.randomUUID(),
  title: "New Research Dossier",
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

  // Research Studio Workbench State
  const [scope, setScope] = useState("all"); // "all" | "files"
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [activeTab, setActiveTab] = useState("evidence"); // "evidence" | "modules" | "citations"
  const [focusedCardId, setFocusedCardId] = useState(null);
  const [focusedEvidenceIndex, setFocusedEvidenceIndex] = useState(null);

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

  // Find the message currently focused in the inspector
  const activeInspectorMessage = useMemo(() => {
    if (focusedCardId) {
      const found = active.messages.find((m) => m.id === focusedCardId && m.role === "assistant");
      if (found) return found;
    }
    // Fallback: latest assistant message with evidence or routing
    for (let i = active.messages.length - 1; i >= 0; i--) {
      if (active.messages[i].role === "assistant" && active.messages[i].id !== "greeting") {
        return active.messages[i];
      }
    }
    return active.messages.find((m) => m.role === "assistant") || GREETING;
  }, [active.messages, focusedCardId]);

  const addFiles = async (list) => {
    const { accepted, errors } = validate(list, queue);
    if (errors.length) {
      setNotice(errors.join(" "));
    }
    if (!accepted.length) return;

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

        // Automatically switch scope to attached documents if files were added
        setScope("files");
      } catch (err) {
        setQueue((q) =>
          q.map((f) =>
            f.id === item.id
              ? { ...f, state: "failed", error: err.message }
              : f
          )
        );
        setNotice(`Failed to index ${item.file.name}: ${err.message}`);
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

    // Respect user's explicit retrieval scope
    const queryFileIds = scope === "files" ? allFileIds : [];
    const activeFilesList = scope === "files" ? queue.map((f) => f.file.name) : [];

    patch((s) => ({
      ...s,
      title:
        s.title === "New Research Dossier"
          ? (prompt || activeFilesList[0] || "Dossier").slice(0, 36)
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
          module_results: [],
          latency_ms: null,
        },
      ],
    }));

    setFocusedCardId(aid);
    setFocusedEvidenceIndex(null);

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
      fileIds: queryFileIds,
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
            module_results: d.module_results || m.module_results || [],
            latency_ms: d.latency_ms ?? m.latency_ms,
          }));
          setInspectorOpen(true);
        } else if (ev === "status") {
          setQueue((q) =>
            q.map((f) =>
              f.fid === d.file ? { ...f, state: d.state || d.status } : f
            )
          );
        } else if (ev === "error") {
          setAssistant((m) => ({
            ...m,
            text: d.message || "An error occurred during query execution.",
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

  const handleSelectCitation = (evidenceIndex) => {
    setFocusedEvidenceIndex(evidenceIndex);
    setActiveTab("evidence");
    setInspectorOpen(true);
  };

  const handleOpenInspector = (tab) => {
    if (tab) setActiveTab(tab);
    setInspectorOpen(true);
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

  const totalSessionFiles = (active.files?.length || 0) + queue.length;

  return (
    <div className={`studio-app ${inspectorOpen ? "with-inspector" : ""}`}>
      {/* Navigation Sidebar */}
      <Sidebar
        open={sidebar}
        onToggle={() => setSidebar((o) => !o)}
        sessions={sessions}
        activeId={activeId}
        onSelect={(id) => {
          setActiveId(id);
          setFocusedCardId(null);
          setFocusedEvidenceIndex(null);
        }}
        files={active.files}
        onNew={() => {
          const s = newSession();
          setSessions((a) => [s, ...a]);
          setActiveId(s.id);
          setQueue([]);
          setFocusedCardId(null);
          setFocusedEvidenceIndex(null);
        }}
      />

      {/* Main Studio Workspace Pane */}
      <main className="studio-workspace" {...dropProps}>
        <header className="studio-header">
          <div className="header-branding">
            <img src="/graft-logo.png" alt="GRAFT Logo" width="34" height="34" />
            <div>
              <div className="title-row">
                <h1>GRAFT Research Studio</h1>
                <span className="version-pill">v0.1.0 • RAPTOR Adaptive RAG</span>
              </div>
              <p>Gated Retrieval Activation Framework with Hierarchical Tree Storage</p>
            </div>
          </div>

          <div className="header-controls">
            <button
              className={`inspector-toggle-btn ${inspectorOpen ? "active" : ""}`}
              onClick={() => setInspectorOpen((o) => !o)}
              title={inspectorOpen ? "Hide Research Inspector" : "Show Research Inspector"}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                <line x1="15" y1="3" x2="15" y2="21"></line>
              </svg>
              <span>{inspectorOpen ? "Hide Inspector" : "Show Inspector"}</span>
            </button>
          </div>
        </header>

        {/* Research Feed */}
        <div className="studio-feed-wrapper">
          <Feed
            messages={active.messages}
            busy={busy}
            selectedCardId={focusedCardId || activeInspectorMessage?.id}
            onFocusCard={(id) => setFocusedCardId(id)}
            onSelectCitation={handleSelectCitation}
            onOpenInspector={handleOpenInspector}
          />
        </div>

        {drag && (
          <div className="dropzone">Drop PDF, DOCX or TXT files to index into ChromaDB</div>
        )}

        {/* Workspace Footer Input Area */}
        <footer className="studio-footer">
          {notice && (
            <div className="notice-banner" role="alert">
              <span>{notice}</span>
              <button className="notice-dismiss" onClick={() => setNotice("")}>✕</button>
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
            scope={scope}
            onScopeChange={setScope}
            hasFiles={totalSessionFiles > 0}
          />
        </footer>
      </main>

      {/* Right Persistent Inspector Pane */}
      <ResearchInspector
        activeMessage={activeInspectorMessage}
        isOpen={inspectorOpen}
        onClose={() => setInspectorOpen(false)}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        focusedEvidenceIndex={focusedEvidenceIndex}
      />
    </div>
  );
}
