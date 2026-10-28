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
#: Default ceiling for the whole synthesis prompt. Local servers with a small
#: context can lower this through ``LLM_MAX_PROMPT_CHARS``.
MAX_PROMPT_CHARS = 16000
GROUNDING_INSTRUCTION = (
    "Answer only from the provided context. If the context does not contain the answer, "
    "say 'Insufficient evidence'."
)

#: Providers served by an OpenAI-compatible /chat/completions endpoint.
#: Maps provider name to (base URL setting, model setting, API-key setting).
_HTTP_PROVIDERS: dict[str, tuple[str, str, str | None]] = {
    "groq": ("groq_base_url", "groq_model", "groq_api_key"),
    "ollama": ("ollama_base_url", "ollama_model", None),
    "llamacpp": ("llamacpp_base_url", "llamacpp_model", None),
}

#: Common spellings accepted for llama.cpp.
_PROVIDER_ALIASES = {
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
    "llama_cpp": "llamacpp",
}


def _render_prompt(query: str, passages: str, modules_json: str) -> str:
    return (
        f"{GROUNDING_INSTRUCTION}\n\n"
        f"User query:\n{query}\n\n"
        f"Retrieved context:\n{passages or '(No context provided)'}\n\n"
        f"Module results (JSON):\n{modules_json}"
    )


def build_prompt(
    query: str,
    context: list[dict[str, Any]],
    module_results: list[dict[str, Any]],
    max_chars: int | None = None,
) -> str:
    """Render already-normalised modules and the first five ranked retrieval hits.

    ``max_chars`` caps the whole prompt. Retrieval hits are added in rank order
    until the next one would exceed the budget; the first hit is truncated if it
    cannot fit on its own. This keeps small-context local models (for example a
    llama.cpp server started with ``--ctx-size 2048``) from rejecting the
    request outright.
    """
    modules_json = json.dumps(module_results, indent=2, sort_keys=True, ensure_ascii=False)
    budget = MAX_PROMPT_CHARS if max_chars is None else max(max_chars, 1)

    selected: list[str] = []
    for index, chunk in enumerate(context[:MAX_CONTEXT_CHUNKS], start=1):
        text = str(chunk.get("text") or "")
        entry = f"[{index}] {text}"
        if selected:
            candidate = "\n\n".join([*selected, entry])
            if len(_render_prompt(query, candidate, modules_json)) > budget:
                break
            selected.append(entry)
            continue

        if len(_render_prompt(query, entry, modules_json)) <= budget:
            selected.append(entry)
            continue

        # The first hit alone is too large: keep as much of it as fits.
        base = _render_prompt(query, "", modules_json)
        overhead = len(base) - len("(No context provided)")
        room = max(1, budget - overhead - len(f"[{index}] "))
        selected.append(f"[{index}] {text[:room]}")
        break

    return _render_prompt(query, "\n\n".join(selected), modules_json)


def _create_http_client(provider: str) -> Callable[[str], str]:
    """Build a client for an OpenAI-compatible chat-completions server.

    Used by Groq (hosted) and by Ollama / llama.cpp (local). Only ``httpx``
    (already a core dependency) is used, so enabling a local model does not
    require installing a provider SDK.
    """
    from config import settings

    base_url_attr, model_attr, key_attr = _HTTP_PROVIDERS[provider]
    base_url = str(getattr(settings, base_url_attr, "") or "").strip().rstrip("/")
    model = str(getattr(settings, model_attr, "") or "").strip()
    if not base_url:
        raise ValueError(f"Configure settings.{base_url_attr} before using {provider}.")
    if not model:
        raise ValueError(f"Configure settings.{model_attr} before using {provider}.")

    key: str | None = None
    if key_attr is not None:
        raw_key = getattr(settings, key_attr, None)
        if not raw_key or not str(raw_key).strip():
            raise ValueError(f"Configure settings.{key_attr} before using {provider}.")
        key = str(raw_key).strip()

    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    timeout = float(settings.llm_timeout_seconds)

    def generate(prompt: str) -> str:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        try:
            import httpx

            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except Exception:
            # Transport and server errors can echo credentials; never propagate them.
            raise RuntimeError(f"The {provider} LLM request failed.") from None

        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"The {provider} LLM returned an empty response.")
        return text

    return generate


def create_llm_client() -> Callable[[str], str]:
    """Explicitly initialise settings.llm_provider; never called by the fallback.

    Gemini and OpenAI use their optional SDKs. Groq, Ollama, and llama.cpp use
    an OpenAI-compatible HTTP endpoint. ``local`` still means an injected
    callable; use ``ollama`` or ``llamacpp`` for those servers.
    """
    from config import settings

    provider = settings.llm_provider.strip().lower()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    if provider == "local":
        raise ValueError("Local models require an injected callable or .generate(prompt) client.")
    if provider in _HTTP_PROVIDERS:
        return _create_http_client(provider)
    if provider not in {"gemini", "openai"}:
        raise ValueError("llm_provider must be gemini, openai, groq, ollama, llamacpp, or local")
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
                text = client.models.generate_content(model=model, contents=prompt).text
            else:
                response = client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}]
                )
                text = response.choices[0].message.content
        except Exception:
            # SDK error messages can contain credentials; do not propagate them.
            raise RuntimeError(f"The {provider} LLM request failed.") from None

        # Both SDKs can return None for an empty completion (a blocked prompt, a
        # safety filter, or no candidate). Returning that unchecked would hand a
        # None to synthesize(), which is annotated to return str.
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"The {provider} LLM returned an empty response.")
        return text

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
        from config import settings

        prompt = build_prompt(
            query, context, normalised, max_chars=settings.llm_max_prompt_chars
        )
        try:
            llm_answer = generate(prompt)
        except Exception:
            raise RuntimeError("LLM answer synthesis failed.") from None
        if not isinstance(llm_answer, str) or not llm_answer.strip():
            raise ValueError("llm_client must return a non-empty string")
        return {"request_id": request_id, "answer": llm_answer, "evidence": evidence}

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
