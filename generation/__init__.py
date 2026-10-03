"""Generation — synthesizes the final answer from query + retrieved context + module outputs.

Contract: docs/interfaces.md sections 8–9.
Without an injected client, synthesis remains deterministic and offline.
Use create_llm_client() explicitly to select a configured external provider.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from typing import Any

from modules.base import ModuleResult


MAX_CONTEXT_CHUNKS = 5
GROUNDING_INSTRUCTION = (
    "Answer only from the provided context. If the context does not contain the answer, "
    "say 'Insufficient evidence'."
)


def build_prompt(
    query: str,
    context: list[dict[str, Any]],
    module_results: list[dict[str, Any]],
) -> str:
    """Render already-normalised modules and the first five ranked retrieval hits."""
    passages = "\n\n".join(
        f"[{index}] {chunk.get('text') or ''}"
        for index, chunk in enumerate(context[:MAX_CONTEXT_CHUNKS], start=1)
    )
    modules_json = json.dumps(module_results, indent=2, sort_keys=True, ensure_ascii=False)
    return (
        f"{GROUNDING_INSTRUCTION}\n\n"
        f"User query:\n{query}\n\n"
        f"Retrieved context:\n{passages or '(No context provided)'}\n\n"
        f"Module results (JSON):\n{modules_json}"
    )


def create_llm_client() -> Callable[[str], str]:
    """Explicitly initialise settings.llm_provider; never called by the fallback.

    Optional SDKs are imported here, and credentials are read only from settings.
    Local models can be injected into synthesize directly; no local loader exists.
    """
    from config import settings

    provider = settings.llm_provider.strip().lower()
    if provider == "local":
        raise ValueError("Local models require an injected callable or .generate(prompt) client.")
    if provider not in {"gemini", "openai"}:
        raise ValueError("llm_provider must be gemini, openai, or local")
    key = settings.gemini_api_key if provider == "gemini" else settings.openai_api_key
    if not key or not key.strip():
        raise ValueError(f"Configure settings.{provider}_api_key before using this provider.")
    try:
        sdk = importlib.import_module("google.genai" if provider == "gemini" else "openai")
    except ImportError:
        package = "google-genai" if provider == "gemini" else "openai"
        raise RuntimeError(f"Install the optional {package} package to use {provider}.") from None

    try:
        client = sdk.Client(api_key=key) if provider == "gemini" else sdk.OpenAI(api_key=key)
    except Exception:
        raise RuntimeError(f"Could not initialise the {provider} LLM client.") from None
    model = settings.gemini_model if provider == "gemini" else settings.openai_model

    def generate(prompt: str) -> str:
        try:
            if provider == "gemini":
                return client.models.generate_content(model=model, contents=prompt).text
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content
        except Exception:
            # SDK error messages can contain credentials; do not propagate them.
            raise RuntimeError(f"The {provider} LLM request failed.") from None

    return generate


def synthesize(
    request_id: str,
    query: str,
    context: list[dict[str, Any]],
    module_results: list[ModuleResult | dict[str, Any]],
    llm_client: Any = None,
) -> dict[str, Any]:
    """Build a final response dict compatible with docs/interfaces.md section 9.

    Returns {"request_id": ..., "answer": ..., "evidence": [...]}.
    llm_client accepts a callable or an object with generate(prompt), returning str.
    None preserves the original deterministic priority rules without provider loading.
    """
    if not request_id or not str(request_id).strip():
        raise ValueError("request_id must be a non-empty string")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    # Normalise module_results to dicts
    normalised: list[dict[str, Any]] = []
    for r in module_results:
        if isinstance(r, ModuleResult):
            normalised.append(r.to_dict())
        elif isinstance(r, dict):
            normalised.append(dict(r))
        else:
            raise TypeError("module_results must contain ModuleResult or dict")

    # Evidence is union of retrieval context + module evidence, deduped by chunk_id
    evidence_map: dict[str, dict[str, Any]] = {}
    for c in context:
        cid = str(c.get("chunk_id") or c.get("node_id") or f"ctx_{len(evidence_map)}")
        evidence_map.setdefault(cid, c)
    for mr in normalised:
        for ev in mr.get("evidence") or []:
            if isinstance(ev, dict):
                cid = str(ev.get("chunk_id") or ev.get("node_id") or f"ev_{len(evidence_map)}")
                evidence_map.setdefault(cid, ev)

    evidence = list(evidence_map.values())

    if llm_client is not None:
        generate = llm_client if callable(llm_client) else getattr(llm_client, "generate", None)
        if not callable(generate):
            raise TypeError("llm_client must be callable or expose .generate(prompt)")
        prompt = build_prompt(query, context, normalised)
        try:
            answer = generate(prompt)
        except Exception:
            raise RuntimeError("LLM answer synthesis failed.") from None
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("llm_client must return a non-empty string")
        return {"request_id": request_id, "answer": answer, "evidence": evidence}

    # Stub answer: prioritise contradiction summary if present, else fact_lookup result,
    # else first context passage.
    answer: str
    for mr in normalised:
        if mr.get("module") == "contradiction_detection":
            result = mr.get("result")
            conflicts = result.get("conflicts") if isinstance(result, dict) else None
            if conflicts:
                answer = str((mr.get("result") or {}).get("summary") or mr.get("result"))
                break
    else:
        answer = ""

    if not answer:
        for mr in normalised:
            if mr.get("module") == "fact_lookup":
                answer = str(mr.get("result") or "")
                break

    if not answer:
        if normalised and isinstance(normalised[0].get("result"), str):
            answer = str(normalised[0]["result"])
        elif context:
            answer = str(context[0].get("text", ""))[:1200]
        else:
            answer = "No answer could be generated from the available context."

    return {"request_id": request_id, "answer": answer, "evidence": evidence}


__all__ = ["build_prompt", "create_llm_client", "synthesize"]
