# Evaluation

GRAFT reports two answer-quality measures side by side; never one alone.

## Token-overlap F1 (`accuracy_f1`)

Deterministic SQuAD-style F1 over lower-cased `\w+` tokens (multiset overlap).
Fast and reproducible, but it rewards reusing the expected wording and can
penalise a correct paraphrase.

## LLM judge score (`judge_score`)

A judge LLM receives the question, the expected/correct answer, and GRAFT's
answer, then returns an integer 0-3:

- 0 — incorrect or unrelated
- 1 — partially relevant but mostly incorrect/incomplete
- 2 — mostly correct with minor omissions or problems
- 3 — fully correct and relevant

The grading prompt lives in `benchmark/prompts.py`. The judge model is
configured in `config.py` (`judge_model` / `judge_provider`, pinned by
default for repeatability). When no API key is available the judge measure is
skipped and reported as `skipped` rather than as 0.

Run the benchmark normally; `results.json` stores both per-query and aggregate
values for both measures.
