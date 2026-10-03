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
from generation import build_prompt, create_llm_client, synthesize
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
        assert (
            "Answer only from the provided context. If the context does not contain the answer, "
            "say 'Insufficient evidence'."
        ) in prompt
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
