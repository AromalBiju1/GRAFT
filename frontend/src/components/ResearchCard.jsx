import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { copyToClipboard, exportMarkdownReport, formatBibTeX } from "../utils/research";

const complexityTheme = {
  simple: { label: "Simple", dotClass: "dot-emerald", badgeClass: "badge-emerald" },
  low: { label: "Simple", dotClass: "dot-emerald", badgeClass: "badge-emerald" },
  moderate: { label: "Moderate", dotClass: "dot-amber", badgeClass: "badge-amber" },
  medium: { label: "Moderate", dotClass: "dot-amber", badgeClass: "badge-amber" },
  complex: { label: "Complex", dotClass: "dot-rose", badgeClass: "badge-rose" },
  high: { label: "Complex", dotClass: "dot-rose", badgeClass: "badge-rose" },
};

export default function ResearchCard({
  message,
  queryText,
  busy,
  isSelected,
  onFocusCard,
  onSelectCitation,
  onOpenInspector,
}) {
  const [copiedType, setCopiedType] = useState(null);

  const routing = message.routing || {};
  const evidence = message.evidence || [];
  const moduleResults = message.module_results || [];
  const latency = message.latency_ms;
  const complexityInfo = complexityTheme[routing.complexity?.toLowerCase()] || {
    label: routing.complexity || "Adaptive",
    badgeClass: "badge-neutral",
  };

  const handleCopy = async (type) => {
    let content = "";
    if (type === "markdown") {
      content = exportMarkdownReport({
        query: queryText,
        text: message.text,
        routing,
        evidence,
        moduleResults,
        latencyMs: latency,
      });
    } else if (type === "bibtex") {
      content = formatBibTeX(evidence);
    }

    const ok = await copyToClipboard(content);
    if (ok) {
      setCopiedType(type);
      setTimeout(() => setCopiedType(null), 2000);
    }
  };

  // Custom text renderer to make [1], [2], [3] interactive citations
  const renderTextWithCitations = (children) => {
    if (typeof children !== "string") return children;

    const parts = [];
    const regex = /\[(\d+)\]/g;
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(children)) !== null) {
      if (match.index > lastIndex) {
        parts.push(children.substring(lastIndex, match.index));
      }
      const citeNum = parseInt(match[1], 10);
      const evidenceIdx = citeNum - 1; // 1-indexed to 0-indexed

      parts.push(
        <button
          key={`cite-${match.index}-${citeNum}`}
          className="citation-ref-btn"
          title={
            evidence[evidenceIdx]?.source
              ? `Source: ${evidence[evidenceIdx].source}`
              : `Citation [${citeNum}]`
          }
          onClick={(e) => {
            e.stopPropagation();
            onFocusCard?.(message.id);
            onSelectCitation?.(evidenceIdx);
          }}
        >
          {citeNum}
        </button>
      );
      lastIndex = regex.lastIndex;
    }

    if (lastIndex < children.length) {
      parts.push(children.substring(lastIndex));
    }

    return parts;
  };

  return (
    <article
      className={`research-card ${isSelected ? "selected" : ""} ${
        message.error ? "card-error" : ""
      }`}
      onClick={() => onFocusCard?.(message.id)}
    >
      {/* Card Header & Controls */}
      <header className="card-header">
        <div className="card-header-left">
          <span className="card-type-tag">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
              <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
            Research Synthesis
          </span>

          {routing.complexity && (
            <span
              className={`pill-badge ${complexityInfo.badgeClass || "badge-neutral"}`}
              title={`Router Confidence: ${
                routing.confidence ? (routing.confidence * 100).toFixed(1) + "%" : "N/A"
              }`}
            >
              <span className={`complexity-dot ${complexityInfo.dotClass || "dot-neutral"}`} />
              {complexityInfo.label}
            </span>
          )}

          {Number.isFinite(routing.retrieval_depth) && (
            <span
              className="pill-badge badge-depth"
              title={
                routing.retrieval_depth === 0
                  ? "Leaves only"
                  : routing.retrieval_depth === 1
                  ? "Leaves + Cluster Summaries"
                  : "Leaves + Summaries + Root"
              }
            >
              Level {routing.retrieval_depth}
            </span>
          )}
        </div>

        <div className="card-header-actions" onClick={(e) => e.stopPropagation()}>
          <button
            className="action-btn"
            onClick={() => handleCopy("markdown")}
            title="Copy full research report as Markdown"
          >
            {copiedType === "markdown" ? (
              <span className="success-copy">✓ Copied</span>
            ) : (
              <>
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
                Copy MD
              </>
            )}
          </button>

          {evidence.length > 0 && (
            <button
              className="action-btn"
              onClick={() => handleCopy("bibtex")}
              title="Copy BibTeX citations"
            >
              {copiedType === "bibtex" ? (
                <span className="success-copy">✓ Copied</span>
              ) : (
                "BibTeX"
              )}
            </button>
          )}

          <button
            className="action-btn action-inspect"
            onClick={() => {
              onFocusCard?.(message.id);
              onOpenInspector?.("evidence");
            }}
            title="Inspect in Research Pane"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <polyline points="9 18 15 12 9 6"></polyline>
            </svg>
            Inspect
          </button>
        </div>
      </header>

      {/* Main Synthesized Body */}
      <div className="card-body">
        {message.error ? (
          <div className="card-error-text">{message.text}</div>
        ) : (
          <ReactMarkdown
            components={{
              p: ({ children }) => <p>{renderTextWithCitations(children)}</p>,
              li: ({ children }) => <li>{renderTextWithCitations(children)}</li>,
            }}
          >
            {message.text || (busy ? "Synthesizing research findings…" : "")}
          </ReactMarkdown>
        )}
      </div>

      {/* Execution Telemetry Footer */}
      <footer className="card-footer" onClick={(e) => e.stopPropagation()}>
        <div className="card-meta-tags">
          {evidence.length > 0 && (
            <button
              className="meta-tag-btn"
              onClick={() => {
                onFocusCard?.(message.id);
                onOpenInspector?.("evidence");
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
              {evidence.length} {evidence.length === 1 ? "source node" : "source nodes"}
            </button>
          )}

          {moduleResults.length > 0 && (
            <button
              className="meta-tag-btn"
              onClick={() => {
                onFocusCard?.(message.id);
                onOpenInspector?.("modules");
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
              </svg>
              {moduleResults.length} reasoning {moduleResults.length === 1 ? "module" : "modules"}
            </button>
          )}

          {routing.activated_modules?.map((m) => (
            <span key={m} className="module-chip">
              {m.replace(/_/g, " ")}
            </span>
          ))}

          {latency && (
            <span className="telemetry-chip">
              {latency} ms
            </span>
          )}
        </div>
      </footer>
    </article>
  );
}
