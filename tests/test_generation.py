"""Tests for answer synthesis (docs/interfaces.md sections 8-9).

These tests pin offline priority rules, evidence dedup, input validation,
grounded prompts, injected clients, and mocked provider dispatch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import settings
from generation import (
    GROUNDING_INSTRUCTION,
    MAX_COMPACT_MODULE_CHARS,
    build_prompt,
    create_llm_client,
    synthesize,
)
from modules.base import ModuleResult
from tests.modules_helpers import passage

REQUEST_ID = "req_001"

CONFLICT_SUMMARY = "Flagged 1 potential conflict."
FACT_TEXT = "Paris is the capital of France."


def _fact_module() -> ModuleResult:
    return ModuleResult(
        request_id=REQUEST_ID,
        module="fact_lookup",
        result=FACT_TEXT,
        evidence=[passage("shared", "shared passage", 0.9)],
        confidence=0.9,
    )


def _contradiction_module(*, conflicts: bool = True) -> ModuleResult:
    return ModuleResult(
        request_id=REQUEST_ID,
        module="contradiction_detection",
        result={
            "conflicts": [{"overlap": 0.5}] if conflicts else [],
            "summary": CONFLICT_SUMMARY,
        },
        evidence=[passage("x", "passage x", 0.8), passage("y", "passage y", 0.8)],
        confidence=0.8,
    )


class TestEmptyInputs:
    def test_no_context_and_no_modules_reports_no_answer(self) -> None:
        out = synthesize(REQUEST_ID, "anything?", [], [])
        assert out["answer"] == "No answer could be generated from the available context."
        assert out["evidence"] == []

    def test_returns_the_documented_response_keys(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("a", "text")], [])
        assert set(out) == {"request_id", "answer", "evidence"}
        assert out["request_id"] == REQUEST_ID


class TestAnswerPriority:
    def test_contradiction_summary_wins_over_fact_lookup(self) -> None:
        out = synthesize(
            REQUEST_ID,
            "conflicts?",
            [passage("x", "x")],
            [_fact_module(), _contradiction_module()],
        )
        assert out["answer"] == CONFLICT_SUMMARY

    def test_contradiction_without_conflicts_does_not_take_priority(self) -> None:
        out = synthesize(
            REQUEST_ID,
            "q",
            [passage("x", "x")],
            [_fact_module(), _contradiction_module(conflicts=False)],
        )
        assert out["answer"] == FACT_TEXT

    def test_fact_lookup_wins_when_no_contradiction(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("a", "text")], [_fact_module()])
        assert out["answer"] == FACT_TEXT

    def test_falls_back_to_first_context_passage(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("a", "context text")], [])
        assert out["answer"] == "context text"


class TestEvidence:
    def test_evidence_is_deduped_by_chunk_id(self) -> None:
        # "shared" appears in both the retrieval context and module evidence.
        out = synthesize(REQUEST_ID, "q", [passage("shared", "shared passage")], [_fact_module()])
        ids = [e["chunk_id"] for e in out["evidence"]]
        assert ids.count("shared") == 1

    def test_context_items_come_before_module_evidence(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("from_context", "c")], [_fact_module()])
        ids = [e["chunk_id"] for e in out["evidence"]]
        assert ids[0] == "from_context"
        assert "shared" in ids[1:]

    def test_all_evidence_is_retained_when_distinct(self) -> None:
        out = synthesize(REQUEST_ID, "q", [passage("x", "x")], [_contradiction_module()])
        ids = {e["chunk_id"] for e in out["evidence"]}
        assert {"x", "y"} <= ids


class TestModuleResultHandling:
    def test_accepts_raw_dicts(self) -> None:
        out = synthesize(REQUEST_ID, "q", [], [{"module": "fact_lookup", "result": "from a dict"}])
        assert out["answer"] == "from a dict"

    def test_accepts_mixed_module_objects_and_dicts(self) -> None:
        out = synthesize(
            REQUEST_ID, "q", [], [_fact_module(), {"module": "other", "result": "dict result"}]
        )
        assert out["answer"] == FACT_TEXT

    def test_rejects_unsupported_module_result_type(self) -> None:
        with pytest.raises(TypeError):
            synthesize(REQUEST_ID, "q", [], ["a bare string"])  # type: ignore[list-item]


class TestValidation:
    @pytest.mark.parametrize("request_id", ["", "   ", None])
    def test_invalid_request_id_raises(self, request_id: str | None) -> None:
        with pytest.raises(ValueError):
            synthesize(request_id, "q", [], [])  # type: ignore[arg-type]

    @pytest.mark.parametrize("query", ["", "   ", None])
    def test_invalid_query_raises(self, query: str | None) -> None:
        with pytest.raises(ValueError):
            synthesize(REQUEST_ID, query, [], [])  # type: ignore[arg-type]


class TestLLMSynthesis:
    def test_callable_receives_grounded_prompt_and_preserves_evidence(self) -> None:
        context = [passage(f"c{i}", f"retrieved passage {i}") for i in range(7)]
        modules = [_fact_module(), {"module": "numeric_reasoning", "result": {"sum": 42}}]
        client = Mock(return_value="Grounded answer")
        out = synthesize(REQUEST_ID, "What is the total?", context, modules, llm_client=client)
        prompt = client.call_args.args[0]
        assert "What is the total?" in prompt
        assert GROUNDING_INSTRUCTION in prompt
        for i in range(5):
            assert f"retrieved passage {i}" in prompt
        assert "retrieved passage 5" not in prompt
        assert "retrieved passage 6" not in prompt
        serialised = prompt.split("Module results (JSON):\n", 1)[1]
        expected = [_fact_module().to_dict(), modules[1]]
        assert json.loads(serialised) == expected
        assert serialised == json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False)
        assert out["answer"] == "Grounded answer"
        assert out["request_id"] == REQUEST_ID
        assert out["evidence"] == context + _fact_module().evidence
        client.assert_called_once()

    def test_generate_object(self) -> None:
        client = SimpleNamespace(generate=Mock(return_value="Insufficient evidence"))
        out = synthesize(REQUEST_ID, "q", [], [], llm_client=client)
        assert out["answer"] == "Insufficient evidence"
        assert "(No context provided)" in client.generate.call_args.args[0]

    def test_explicit_none_never_loads_a_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("generation.importlib.import_module", Mock(side_effect=AssertionError))
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "not-a-real-key")
        out = synthesize(REQUEST_ID, "q", [], [_fact_module()], llm_client=None)
        assert out == {
            "request_id": REQUEST_ID,
            "answer": FACT_TEXT,
            "evidence": _fact_module().evidence,
        }

    @pytest.mark.parametrize("client", [object(), SimpleNamespace(generate="not callable")])
    def test_invalid_client(self, client: object) -> None:
        with pytest.raises(TypeError, match="llm_client"):
            synthesize(REQUEST_ID, "q", [], [], llm_client=client)

    @pytest.mark.parametrize("answer", [None, {}, "", "  "])
    def test_invalid_answer(self, answer: object) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            synthesize(REQUEST_ID, "q", [], [], llm_client=lambda prompt: answer)

    def test_client_error_does_not_expose_credentials(self) -> None:
        client = Mock(side_effect=RuntimeError("secret-api-key"))
        with pytest.raises(RuntimeError, match="LLM answer synthesis failed") as exc:
            synthesize(REQUEST_ID, "q", [], [], llm_client=client)
        assert "secret-api-key" not in str(exc.value)
        assert exc.value.__suppress_context__

    def test_prompt_handles_missing_text(self) -> None:
        assert "[1] " in build_prompt("q", [{"chunk_id": "a"}], [])

    def test_prompt_does_not_prime_the_model_with_a_refusal_phrase(self) -> None:
        prompt = build_prompt("q", [passage("c0", "answer")], [])

        assert "Insufficient evidence" not in prompt

    def test_prompt_budget_truncates_the_first_passage(self) -> None:
        context = [passage("c0", "x" * 5000), passage("c1", "y" * 5000)]

        prompt = build_prompt("q", context, [], max_chars=1000)

        assert len(prompt) <= 1000
        assert "[1] " in prompt
        assert "[2]" not in prompt

    def test_prompt_budget_keeps_ranked_hits_that_fit(self) -> None:
        context = [passage(f"c{i}", "x" * 100) for i in range(5)]

        prompt = build_prompt("q", context, [], max_chars=1000)

        assert len(prompt) <= 1000
        assert "[1] " in prompt
        assert "[5] " in prompt

    def test_prompt_budget_shrinks_oversized_module_results(self) -> None:
        modules = [
            {
                "module": "fact_lookup",
                "confidence": 0.9,
                "result": "x" * 5000,
                "evidence": [{"text": "y" * 5000}],
            }
        ]
        context = [passage("c0", "retrieved context " * 20)]

        prompt = build_prompt("q", context, modules, max_chars=1200)

        assert len(prompt) <= 1200
        serialised = prompt.split("Module results (JSON):\n", 1)[1]
        parsed = json.loads(serialised)
        assert parsed[0]["module"] == "fact_lookup"
        assert parsed[0]["confidence"] == 0.9
        assert len(parsed[0]["result"]) <= MAX_COMPACT_MODULE_CHARS
        assert "evidence" not in parsed[0]
        assert "retrieved context" in prompt

    def test_prompt_budget_drops_modules_when_compaction_is_not_enough(self) -> None:
        modules = [
            {"module": f"m{i}", "confidence": 0.5, "result": "x" * 2000}
            for i in range(20)
        ]

        prompt = build_prompt("q", [], modules, max_chars=600)

        assert len(prompt) <= 600
        serialised = prompt.split("Module results (JSON):\n", 1)[1]
        assert json.loads(serialised) == []

    def test_prompt_budget_truncates_a_huge_query(self) -> None:
        prompt = build_prompt("q" * 5000, [], [], max_chars=400)

        assert len(prompt) <= 400
        assert "User query:" in prompt

    def test_synthesize_uses_the_prompt_budget_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_max_prompt_chars", 900)
        client = Mock(return_value="Grounded answer")
        context = [passage("c0", "x" * 5000)]

        synthesize(REQUEST_ID, "q", context, [], llm_client=client)

        assert len(client.call_args.args[0]) <= 900


class TestProviderFactory:
    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_provider_dispatch(self, provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", provider)
        monkeypatch.setattr(settings, f"{provider}_api_key", "test-only-key")
        monkeypatch.setattr(settings, f"{provider}_model", "test-model")
        sdk = Mock()
        if provider == "gemini":
            generate = sdk.Client.return_value.models.generate_content
            generate.return_value = SimpleNamespace(text="Gemini answer")
        else:
            generate = sdk.OpenAI.return_value.chat.completions.create
            generate.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OpenAI answer"))]
            )
        importer = Mock(return_value=sdk)
        monkeypatch.setattr("generation.importlib.import_module", importer)
        client = create_llm_client()
        importer.assert_called_once_with("google.genai" if provider == "gemini" else "openai")
        constructor = sdk.Client if provider == "gemini" else sdk.OpenAI
        constructor.assert_called_once_with(api_key="test-only-key")
        generate.assert_not_called()
        assert client("prompt") == ("Gemini answer" if provider == "gemini" else "OpenAI answer")
        if provider == "gemini":
            generate.assert_called_once_with(model="test-model", contents="prompt")
        else:
            generate.assert_called_once_with(
                model="test-model", messages=[{"role": "user", "content": "prompt"}]
            )
        generate.side_effect = RuntimeError("test-only-key")
        with pytest.raises(RuntimeError, match="LLM request failed") as exc:
            client("prompt")
        assert "test-only-key" not in str(exc.value)

    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_missing_key_does_not_import_sdk(
        self, provider: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", provider)
        monkeypatch.setattr(settings, f"{provider}_api_key", None)
        importer = Mock()
        monkeypatch.setattr("generation.importlib.import_module", importer)
        with pytest.raises(ValueError, match=f"settings.{provider}_api_key"):
            create_llm_client()
        importer.assert_not_called()

    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_missing_sdk_is_optional(self, provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", provider)
        monkeypatch.setattr(settings, f"{provider}_api_key", "test-only-key")
        monkeypatch.setattr("generation.importlib.import_module", Mock(side_effect=ImportError))
        with pytest.raises(RuntimeError, match="optional"):
            create_llm_client()
        assert synthesize(REQUEST_ID, "q", [], [])["answer"].startswith("No answer")

    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_empty_completion_is_rejected(
        self, provider: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An SDK returning None must raise, not hand None back as a str.

        Both SDKs yield None for an empty completion (blocked prompt, safety
        filter, no candidate). synthesize() is annotated to return str.
        """
        monkeypatch.setattr(settings, "llm_provider", provider)
        monkeypatch.setattr(settings, f"{provider}_api_key", "test-only-key")

        if provider == "gemini":
            sdk = SimpleNamespace(
                Client=Mock(
                    return_value=SimpleNamespace(
                        models=SimpleNamespace(
                            generate_content=Mock(return_value=SimpleNamespace(text=None))
                        )
                    )
                )
            )
        else:
            message = SimpleNamespace(content=None)
            sdk = SimpleNamespace(
                OpenAI=Mock(
                    return_value=SimpleNamespace(
                        chat=SimpleNamespace(
                            completions=SimpleNamespace(
                                create=Mock(
                                    return_value=SimpleNamespace(
                                        choices=[SimpleNamespace(message=message)]
                                    )
                                )
                            )
                        )
                    )
                )
            )
        monkeypatch.setattr("generation.importlib.import_module", Mock(return_value=sdk))

        generate = create_llm_client()
        with pytest.raises(RuntimeError, match="empty response"):
            generate("a prompt")

    def test_whitespace_only_completion_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "test-only-key")
        message = SimpleNamespace(content="   \n  ")
        sdk = SimpleNamespace(
            OpenAI=Mock(
                return_value=SimpleNamespace(
                    chat=SimpleNamespace(
                        completions=SimpleNamespace(
                            create=Mock(
                                return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)])
                            )
                        )
                    )
                )
            )
        )
        monkeypatch.setattr("generation.importlib.import_module", Mock(return_value=sdk))

        generate = create_llm_client()
        with pytest.raises(RuntimeError, match="empty response"):
            generate("a prompt")

    def test_initialization_error_is_sanitized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "openai_api_key", "test-only-key")
        sdk = SimpleNamespace(OpenAI=Mock(side_effect=RuntimeError("test-only-key")))
        monkeypatch.setattr("generation.importlib.import_module", Mock(return_value=sdk))
        with pytest.raises(RuntimeError, match="initialise") as exc:
            create_llm_client()
        assert "test-only-key" not in str(exc.value)

    @pytest.mark.parametrize("provider", ["local", "unsupported"])
    def test_unsupported_provider_is_explicit(
        self, provider: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", provider)
        with pytest.raises(ValueError, match="injected|llm_provider"):
            create_llm_client()


class TestHTTPProviders:
    """Groq, Ollama, and llama.cpp all use OpenAI-compatible HTTP endpoints."""

    def _mock_post(self, monkeypatch: pytest.MonkeyPatch, content: object = "HTTP answer") -> Mock:
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"choices": [{"message": {"content": content}}]}
        post = Mock(return_value=response)
        monkeypatch.setattr("httpx.post", post)
        return post

    def test_groq_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", "groq")
        monkeypatch.setattr(settings, "groq_api_key", "test-only-key")
        monkeypatch.setattr(settings, "groq_model", "openai/gpt-oss-120b")
        monkeypatch.setattr(settings, "groq_base_url", "https://api.groq.test/openai/v1/")
        monkeypatch.setattr(settings, "llm_timeout_seconds", 30.0)
        post = self._mock_post(monkeypatch, "Groq answer")

        client = create_llm_client()

        assert client("a prompt") == "Groq answer"
        post.assert_called_once_with(
            "https://api.groq.test/openai/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-only-key",
            },
            json={
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": "a prompt"}],
                "stream": False,
            },
            timeout=30.0,
        )

    def test_ollama_dispatch_has_no_authorization_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", "ollama")
        monkeypatch.setattr(settings, "ollama_model", "llama3.2:3b")
        monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434/v1")
        post = self._mock_post(monkeypatch, "Ollama answer")

        client = create_llm_client()

        assert client("prompt") == "Ollama answer"
        assert post.call_args.kwargs["headers"] == {"Content-Type": "application/json"}
        assert post.call_args.args[0] == "http://127.0.0.1:11434/v1/chat/completions"

    @pytest.mark.parametrize("provider", ["llamacpp", "llama.cpp", "llama-cpp", "llama_cpp"])
    def test_llamacpp_dispatch_accepts_aliases(
        self, provider: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", provider)
        monkeypatch.setattr(settings, "llamacpp_model", "graft-local")
        monkeypatch.setattr(settings, "llamacpp_base_url", "http://127.0.0.1:8081/v1")
        post = self._mock_post(monkeypatch, "llama.cpp answer")

        client = create_llm_client()

        assert client("prompt") == "llama.cpp answer"
        assert post.call_args.kwargs["headers"] == {"Content-Type": "application/json"}
        assert post.call_args.args[0] == "http://127.0.0.1:8081/v1/chat/completions"

    def test_local_defaults_are_small_and_use_the_dev_server(self) -> None:
        from config import Settings

        fields = Settings.model_fields
        assert fields["llamacpp_base_url"].default == "http://127.0.0.1:8081/v1"
        assert fields["llamacpp_model"].default == "graft-local"
        assert fields["ollama_model"].default == "llama3.2:3b"
        assert fields["llamacpp_model_path"].default is None

    def test_missing_groq_key_does_not_make_a_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", "groq")
        monkeypatch.setattr(settings, "groq_api_key", None)
        post = self._mock_post(monkeypatch)

        with pytest.raises(ValueError, match="settings.groq_api_key"):
            create_llm_client()
        post.assert_not_called()

    @pytest.mark.parametrize("provider", ["ollama", "llamacpp"])
    @pytest.mark.parametrize("attribute", ["base_url", "model"])
    def test_local_provider_requires_endpoint_and_model(
        self, provider: str, attribute: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", provider)
        monkeypatch.setattr(settings, f"{provider}_{attribute}", "")
        with pytest.raises(ValueError, match=f"settings.{provider}_{attribute}"):
            create_llm_client()

    @pytest.mark.parametrize("content", [None, "", "   "])
    def test_empty_http_completion_is_rejected(
        self, content: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", "ollama")
        self._mock_post(monkeypatch, content)

        with pytest.raises(RuntimeError, match="empty response"):
            create_llm_client()("prompt")

    def test_http_error_does_not_expose_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", "groq")
        monkeypatch.setattr(settings, "groq_api_key", "secret-api-key")
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("secret-api-key")
        monkeypatch.setattr("httpx.post", Mock(return_value=response))

        with pytest.raises(RuntimeError, match="LLM request failed") as exc:
            create_llm_client()("prompt")
        assert "secret-api-key" not in str(exc.value)
        assert exc.value.__suppress_context__

    def test_invalid_json_shape_is_a_sanitized_request_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", "llamacpp")
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"unexpected": "shape"}
        monkeypatch.setattr("httpx.post", Mock(return_value=response))

        with pytest.raises(RuntimeError, match="LLM request failed"):
            create_llm_client()("prompt")
