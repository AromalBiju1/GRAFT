import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import RoutingBadges from "../RoutingBadges";
import EvidenceList from "../EvidenceList";

export default function Feed({ messages, busy }) {
  const end = useRef(null);
  const box = useRef(null);

  useEffect(() => {
    // follow the stream unless the user scrolled up
    const b = box.current;
    if (b && b.scrollHeight - b.scrollTop - b.clientHeight < 120) {
      end.current?.scrollIntoView({ block: "end" });
    }
  }, [messages]);

  return (
    <div className="feed" ref={box} role="log" aria-live="polite">
      {messages.map((m) => (
        <div key={m.id} className={`msg ${m.role} ${m.error ? "err" : ""}`}>
          {m.role === "user" ? (
            <>
              {m.files?.length > 0 && (
                <div className="msg-files">
                  {m.files.map((n) => (
                    <span key={n}>{n}</span>
                  ))}
                </div>
              )}
              {m.text}
            </>
          ) : (
            <>
              {m.routing && <RoutingBadges routing={m.routing} />}
              <ReactMarkdown>{m.text || (busy ? "…" : "")}</ReactMarkdown>
              {Array.isArray(m.evidence) && m.evidence.length > 0 && (
                <details className="evidence-accordion">
                  <summary>
                    Inspect Evidence ({m.evidence.length}{" "}
                    {m.evidence.length === 1 ? "source" : "sources"})
                  </summary>
                  <EvidenceList evidence={m.evidence} />
                </details>
              )}
            </>
          )}
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}
