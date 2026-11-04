import { useState, useEffect, useRef } from "react";
import { copyToClipboard, formatAPA, formatBibTeX } from "../utils/research";

export default function ResearchInspector({
  activeMessage,
  isOpen,
  onClose,
  activeTab = "evidence",
  onTabChange,
  focusedEvidenceIndex = null,
}) {
  const [expandedTextIds, setExpandedTextIds] = useState(new Set());
  const [copiedType, setCopiedType] = useState(null);
  const evidenceListRef = useRef(null);

  const evidence = activeMessage?.evidence || [];
  const moduleResults = activeMessage?.module_results || [];
  const routing = activeMessage?.routing || {};
  const latency = activeMessage?.latency_ms;

  // Auto-scroll to focused evidence when citation is clicked
  useEffect(() => {
    if (focusedEvidenceIndex !== null && activeTab === "evidence") {
      const el = document.getElementById(`evidence-item-${focusedEvidenceIndex}`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }, [focusedEvidenceIndex, activeTab]);

  const toggleExpandText = (idx) => {
    setExpandedTextIds((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const handleCopy = async (type) => {
    let content = "";
    if (type === "bibtex") content = formatBibTeX(evidence);
    else if (type === "apa") content = formatAPA(evidence);
    else if (type === "json") content = JSON.stringify(activeMessage, null, 2);

    const ok = await copyToClipboard(content);
    if (ok) {
      setCopiedType(type);
      setTimeout(() => setCopiedType(null), 2000);
    }
  };

  const getLevelBadge = (level) => {
    if (level === 0) return { label: "Level 0 Leaf", desc: "Raw text chunk", cls: "tree-leaf" };
    if (level === 1) return { label: "Level 1 Summary", desc: "RAPTOR cluster summary", cls: "tree-summary" };
    if (level === 2) return { label: "Level 2 Root", desc: "Top-level root summary", cls: "tree-root" };
    return { label: `Level ${level ?? 0}`, desc: "Hierarchical tree node", cls: "tree-leaf" };
  };

  if (!isOpen) return null;

  return (
    <aside className="research-inspector" aria-label="Research Inspector">
      {/* Inspector Top Bar */}
      <div className="inspector-header">
        <div className="inspector-title-row">
          <div className="inspector-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
              <polyline points="2 17 12 22 22 17"></polyline>
              <polyline points="2 12 12 17 22 12"></polyline>
            </svg>
            <span>Research Inspector</span>
          </div>
          <button className="inspector-close-btn" onClick={onClose} aria-label="Close inspector">
            ✕
          </button>
        </div>

        {/* Tab Navigation */}
        <nav className="inspector-tabs">
          <button
            className={`inspector-tab ${activeTab === "evidence" ? "active" : ""}`}
            onClick={() => onTabChange("evidence")}
          >
            Evidence & Tree ({evidence.length})
          </button>
          <button
            className={`inspector-tab ${activeTab === "modules" ? "active" : ""}`}
            onClick={() => onTabChange("modules")}
          >
            Reasoning ({moduleResults.length})
          </button>
          <button
            className={`inspector-tab ${activeTab === "citations" ? "active" : ""}`}
            onClick={() => onTabChange("citations")}
          >
            Citations & Trace
          </button>
        </nav>
      </div>

      {/* Inspector Body Content */}
      <div className="inspector-body" ref={evidenceListRef}>
        {/* Tab 1: Evidence & Tree Hierarchy */}
        {activeTab === "evidence" && (
          <div className="tab-pane evidence-pane">
            {evidence.length === 0 ? (
              <div className="empty-state">
                <p>No supporting evidence records available for this query.</p>
              </div>
            ) : (
              <div className="evidence-cards-list">
                {evidence.map((item, idx) => {
                  const levelInfo = getLevelBadge(item.level);
                  const isFocused = focusedEvidenceIndex === idx;
                  const isExpanded = expandedTextIds.has(idx);
                  const scorePct = Number.isFinite(item.score)
                    ? Math.max(0, Math.min(100, item.score * 100))
                    : null;

                  return (
                    <div
                      key={`${item.node_id || item.chunk_id || "ev"}-${idx}`}
                      id={`evidence-item-${idx}`}
                      className={`evidence-card ${isFocused ? "focused" : ""}`}
                    >
                      <div className="evidence-card-header">
                        <span className="evidence-idx-pill">[{idx + 1}]</span>
                        <span className={`tree-level-tag ${levelInfo.cls}`} title={levelInfo.desc}>
                          {levelInfo.label}
                        </span>
                        {item.page && <span className="source-page-tag">p. {item.page}</span>}
                      </div>

                      <div className="evidence-source-title" title={item.source || item.document_id || ""}>
                        📄 {item.source || item.document_id || "Corpus Document"}
                      </div>

                      {scorePct !== null && (
                        <div className="similarity-meter" title={`Cosine Relevance: ${scorePct.toFixed(1)}%`}>
                          <div className="meter-label">
                            <span>Relevance Match</span>
                            <strong>{scorePct.toFixed(1)}%</strong>
                          </div>
                          <div className="meter-track">
                            <div className="meter-fill" style={{ width: `${scorePct}%` }}></div>
                          </div>
                        </div>
                      )}

                      <div className="evidence-text-preview">
                        <p className={`passage-quote ${isExpanded ? "expanded" : ""}`}>
                          {item.text || "No text snippet recorded."}
                        </p>
                        {item.text && item.text.length > 200 && (
                          <button
                            className="expand-text-toggle"
                            onClick={() => toggleExpandText(idx)}
                          >
                            {isExpanded ? "Show less" : "Show full passage"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Tab 2: Specialist Reasoning Modules */}
        {activeTab === "modules" && (
          <div className="tab-pane modules-pane">
            <div className="routing-overview-card">
              <h4>Gated Router Decision</h4>
              <div className="routing-grid">
                <div>
                  <span className="meta-label">Complexity</span>
                  <strong>{routing.complexity || "Simple"}</strong>
                </div>
                <div>
                  <span className="meta-label">Confidence</span>
                  <strong>{routing.confidence ? (routing.confidence * 100).toFixed(1) + "%" : "N/A"}</strong>
                </div>
                <div>
                  <span className="meta-label">Retrieval Depth</span>
                  <strong>Level {routing.retrieval_depth ?? 0}</strong>
                </div>
                {latency && (
                  <div>
                    <span className="meta-label">Latency</span>
                    <strong>{latency} ms</strong>
                  </div>
                )}
              </div>
            </div>

            {moduleResults.length === 0 ? (
              <div className="empty-state">
                <p>No specialist reasoning modules were triggered for this query complexity.</p>
              </div>
            ) : (
              <div className="modules-list">
                {moduleResults.map((mod, i) => {
                  const modName = mod.module || `module_${i + 1}`;
                  return (
                    <div key={modName} className="module-result-card">
                      <div className="module-card-header">
                        <h5>{modName.replace(/_/g, " ").toUpperCase()}</h5>
                        {Number.isFinite(mod.confidence) && (
                          <span className="module-conf-pill">
                            Confidence: {(mod.confidence * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>

                      <div className="module-card-content">
                        {/* Contradiction Detection Special View */}
                        {modName === "contradiction_detection" && (
                          <div className="contradiction-box">
                            {mod.result?.conflicts && mod.result.conflicts.length > 0 ? (
                              <>
                                <span className="conflict-badge alert">Conflicts Detected</span>
                                <ul className="conflict-items">
                                  {mod.result.conflicts.map((c, cIdx) => (
                                    <li key={cIdx}>{typeof c === "string" ? c : JSON.stringify(c)}</li>
                                  ))}
                                </ul>
                              </>
                            ) : (
                              <span className="conflict-badge ok">No Direct Contradictions</span>
                            )}
                            {mod.result?.summary && (
                              <p className="summary-desc">{mod.result.summary}</p>
                            )}
                          </div>
                        )}

                        {/* Numeric Reasoning Special View */}
                        {modName === "numeric_reasoning" && (
                          <div className="numeric-box">
                            {typeof mod.result === "object" ? (
                              <>
                                {mod.result.calculation && (
                                  <div className="formula-line">
                                    <code>{mod.result.calculation}</code>
                                  </div>
                                )}
                                {mod.result.answer && (
                                  <div className="calc-result">
                                    Result: <strong>{mod.result.answer}</strong>
                                  </div>
                                )}
                              </>
                            ) : (
                              <p>{String(mod.result)}</p>
                            )}
                          </div>
                        )}

                        {/* Multi-Hop Reasoning Special View */}
                        {modName === "multi_hop" && (
                          <div className="multihop-box">
                            {Array.isArray(mod.result?.hops) ? (
                              <ol className="hop-chain">
                                {mod.result.hops.map((hop, hIdx) => (
                                  <li key={hIdx} className="hop-step">
                                    {typeof hop === "string" ? hop : JSON.stringify(hop)}
                                  </li>
                                ))}
                              </ol>
                            ) : (
                              <p>{typeof mod.result === "string" ? mod.result : JSON.stringify(mod.result)}</p>
                            )}
                          </div>
                        )}

                        {/* Fact Lookup Special View */}
                        {modName === "fact_lookup" && (
                          <div className="fact-box">
                            <p>{typeof mod.result === "string" ? mod.result : JSON.stringify(mod.result)}</p>
                          </div>
                        )}

                        {/* Fallback for other modules */}
                        {!["contradiction_detection", "numeric_reasoning", "multi_hop", "fact_lookup"].includes(modName) && (
                          <pre className="json-raw">{JSON.stringify(mod.result, null, 2)}</pre>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Tab 3: Citations & Audit Trace */}
        {activeTab === "citations" && (
          <div className="tab-pane citations-pane">
            <div className="citation-export-section">
              <div className="section-header-row">
                <h4>BibTeX Reference Block</h4>
                <button
                  className="copy-snippet-btn"
                  onClick={() => handleCopy("bibtex")}
                >
                  {copiedType === "bibtex" ? "✓ Copied" : "Copy BibTeX"}
                </button>
              </div>
              <pre className="citation-code-block">{formatBibTeX(evidence)}</pre>
            </div>

            <div className="citation-export-section">
              <div className="section-header-row">
                <h4>APA Style References</h4>
                <button
                  className="copy-snippet-btn"
                  onClick={() => handleCopy("apa")}
                >
                  {copiedType === "apa" ? "✓ Copied" : "Copy APA"}
                </button>
              </div>
              <pre className="citation-code-block">{formatAPA(evidence)}</pre>
            </div>

            <div className="citation-export-section">
              <div className="section-header-row">
                <h4>Engine Execution Trace (JSON)</h4>
                <button
                  className="copy-snippet-btn"
                  onClick={() => handleCopy("json")}
                >
                  {copiedType === "json" ? "✓ Copied" : "Copy JSON"}
                </button>
              </div>
              <pre className="citation-code-block json-trace">
                {JSON.stringify(
                  {
                    request_id: activeMessage?.id,
                    latency_ms: latency,
                    routing,
                    module_results: moduleResults,
                    evidence_count: evidence.length,
                  },
                  null,
                  2
                )}
              </pre>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
