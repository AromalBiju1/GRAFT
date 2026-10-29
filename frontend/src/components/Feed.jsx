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
            <div key={m.id} className="user-query-row">
              <div className="user-query-bubble">
                {m.files?.length > 0 && (
                  <div className="user-query-attachments">
                    {m.files.map((name) => (
                      <span key={name} className="attached-file-chip">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                          <polyline points="14 2 14 8 20 8"></polyline>
                        </svg>
                        {name}
                      </span>
                    ))}
                  </div>
                )}
                <div className="user-query-text">{m.text}</div>
              </div>
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
