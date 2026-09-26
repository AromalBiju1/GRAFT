"""Prompt templates for the RAPTOR indexing pipeline."""

SUMMARIZATION_PROMPT = """You are a precise technical summarizer building a hierarchical index.
Summarize the following related text passages into a cohesive, standalone summary. 
Retain critical factual details, names, entities, and numeric claims.

Passages:
{concatenated_texts}

Cohesive Summary:"""


def format_summarization_prompt(concatenated_texts: str) -> str:
    """Render ``SUMMARIZATION_PROMPT`` with the given concatenated passages."""
    return SUMMARIZATION_PROMPT.format(concatenated_texts=concatenated_texts)


__all__ = ["SUMMARIZATION_PROMPT", "format_summarization_prompt"]
