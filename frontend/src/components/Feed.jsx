import { useEffect, useRef } from "react";
import ResearchCard from "./ResearchCard";

export default function Feed({
  messages,
  busy,
  selectedCardId,
  onFocusCard,
  onSelectCitation,
  onOpenInspector,
}) {
  const end = useRef(null);
  const box = useRef(null);

  useEffect(() => {
    const b = box.current;
    if (b && b.scrollHeight - b.scrollTop - b.clientHeight < 160) {
      end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
    }
  }, [messages, busy]);

  // Find the query corresponding to each assistant turn
  const getQueryForMessage = (idx) => {
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i]?.role === "user") {
        return messages[i].text;
      }
    }
    return "Research Query";
  };

  return (
    <div className="feed" ref={box} role="log" aria-live="polite">
      {messages.map((m, idx) => {
        if (m.role === "user") {
          return (
            <div key={m.id} className="user-query-container">
              <div className="query-meta-header">
                <span className="query-avatar">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="4"></circle>
                    <path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"></path>
                  </svg>
                  Researcher Query
                </span>
                {m.files?.length > 0 && (
                  <div className="query-attached-files">
                    {m.files.map((name) => (
                      <span key={name} className="attached-file-pill">
                        📄 {name}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div className="query-content">{m.text}</div>
            </div>
          );
        }

        // Assistant turn -> Full structured Research Card
        return (
          <ResearchCard
            key={m.id}
            message={m}
            queryText={getQueryForMessage(idx)}
            busy={busy && idx === messages.length - 1}
            isSelected={selectedCardId === m.id}
            onFocusCard={onFocusCard}
            onSelectCitation={onSelectCitation}
            onOpenInspector={onOpenInspector}
          />
        );
      })}
      <div ref={end} />
    </div>
  );
}
