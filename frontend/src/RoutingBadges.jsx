const complexityStyles = {
  simple: "green", low: "green",
  moderate: "yellow", medium: "yellow",
  complex: "red", high: "red",
};

export default function RoutingBadges({ routing }) {
  if (!routing || typeof routing !== "object") return null;
  const complexity = typeof routing.complexity === "string" ? routing.complexity : null;
  const modules = Array.isArray(routing.activated_modules)
    ? routing.activated_modules.filter((module) => typeof module === "string" && module) : null;

  return (
    <section className="routing" aria-label="Query routing">
      {complexity && <span className={`badge ${complexityStyles[complexity.toLowerCase()] ?? ""}`}>Complexity: {complexity}</span>}
      {Number.isFinite(routing.retrieval_depth) && <span className="badge">Retrieval depth: {routing.retrieval_depth}</span>}
      {modules?.map((module, index) => <span className="badge" key={`${module}-${index}`}>Module: {module.replaceAll("_", " ")}</span>)}
      {modules?.length === 0 && <span className="badge">No modules activated</span>}
    </section>
  );
}
