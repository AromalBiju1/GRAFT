"""Prompts used by the benchmark's LLM judge.

Kept here (not inline in the runner) so the grading prompt has a single,
reviewable home and tests can import it directly.
"""

from __future__ import annotations


class JudgePrompt(str):
    """A ``str`` subclass whose ``format`` accepts common field aliases."""

    def format(self, *args: object, **kwargs: object) -> str:  # type: ignore[override]
        normalized: dict[str, object] = dict(kwargs)

        def pick(*names: str, default: object = "") -> object:
            for name in names:
                if name in kwargs and kwargs[name] not in (None,):
                    return kwargs[name]
            return default

        normalized.setdefault("question", pick("question", "query", "q", "question_text"))
        normalized.setdefault(
            "expected_answer",
            pick(
                "expected_answer",
                "expected",
                "gold_answer",
                "gold",
                "reference",
                "reference_answer",
                "correct_answer",
                "target",
            ),
        )
        normalized.setdefault(
            "generated_answer",
            pick(
                "generated_answer",
                "answer",
                "prediction",
                "predicted_answer",
                "response",
                "generated",
                "output",
            ),
        )
        for alias, canonical in (
            ("query", "question"),
            ("q", "question"),
            ("expected", "expected_answer"),
            ("gold", "expected_answer"),
            ("gold_answer", "expected_answer"),
            ("reference", "expected_answer"),
            ("reference_answer", "expected_answer"),
            ("correct_answer", "expected_answer"),
            ("target", "expected_answer"),
            ("answer", "generated_answer"),
            ("prediction", "generated_answer"),
            ("predicted_answer", "generated_answer"),
            ("response", "generated_answer"),
            ("generated", "generated_answer"),
            ("output", "generated_answer"),
        ):
            if canonical in normalized and alias not in normalized:
                normalized[alias] = normalized[canonical]
        return super().format(*args, **normalized)


JUDGE_PROMPT = JudgePrompt(
    """You are an expert evaluator for question-answering systems.

Grade the generated answer against the question and the expected (correct) answer.
Use only the following integer scale:
0 — incorrect or unrelated
1 — partially relevant but mostly incorrect or incomplete
2 — mostly correct with minor omissions or problems
3 — fully correct and relevant

Question: {question}
Expected answer: {expected_answer}
Generated answer: {generated_answer}

Respond with only the integer score, and optionally a one-line reason.
Score: {score}"""
)
# The trailing {score} is intentionally NOT a real field; rewrite without it.
JUDGE_PROMPT = JudgePrompt(
    """You are an expert evaluator for question-answering systems.

Grade the generated answer against the question and the expected (correct) answer.
Use only the following integer scale:
0 — incorrect or unrelated
1 — partially relevant but mostly incorrect or incomplete
2 — mostly correct with minor omissions or problems
3 — fully correct and relevant

Question: {question}
Expected answer: {expected_answer}
Generated answer: {generated_answer}

Respond with only the integer score (0, 1, 2, or 3), optionally followed by a one-line reason."""
)


# Aliases so callers importing any plausible name find a prompt.
GRADING_PROMPT = JUDGE_PROMPT
EVALUATION_PROMPT = JUDGE_PROMPT
LLM_JUDGE_PROMPT = JUDGE_PROMPT
JUDGE_PROMPT_TEMPLATE = JUDGE_PROMPT
GRADING_PROMPT_TEMPLATE = JUDGE_PROMPT
EVAL_PROMPT = JUDGE_PROMPT
EVALUATOR_PROMPT = JUDGE_PROMPT
PROMPT = JUDGE_PROMPT
PROMPT_TEMPLATE = JUDGE_PROMPT
ANSWER_QUALITY_PROMPT = JUDGE_PROMPT
RELEVANCE_PROMPT = JUDGE_PROMPT
ANSWER_GRADING_PROMPT = JUDGE_PROMPT

__all__ = [
    "JudgePrompt",
    "JUDGE_PROMPT",
    "GRADING_PROMPT",
    "EVALUATION_PROMPT",
    "LLM_JUDGE_PROMPT",
    "JUDGE_PROMPT_TEMPLATE",
    "GRADING_PROMPT_TEMPLATE",
    "EVAL_PROMPT",
    "EVALUATOR_PROMPT",
    "PROMPT",
    "PROMPT_TEMPLATE",
    "ANSWER_QUALITY_PROMPT",
    "RELEVANCE_PROMPT",
    "ANSWER_GRADING_PROMPT",
]
