# References

## RAPTOR & Hierarchical Retrieval

- Sarthi et al., *RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval* (ICLR 2024). — Hierarchical summarisation tree that GRAFT adapts.
- Lewis et al., *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks* (NeurIPS 2020). — Original RAG formulation.
- Gao et al., *Retrieval-Augmented Generation for Large Language Models: A Survey* (2023).

## Adaptive / Gated Retrieval

- Jeong et al., *Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models through Question Complexity* (NAACL 2024). — Complexity-aware routing for RAG.
- Asai et al., *Self-RAG: Learning to Retrieve, Generate, and Critique Through Self-Reflection* (ICLR 2024).
- Yan et al., *Corrective Retrieval Augmented Generation (CRAG)* (2024).

## Mixture-of-Experts & Gating

- Shazeer et al., *Outrageously Large Neural Networks: The Sparsely-Gated Mixture-of-Experts Layer* (ICLR 2017).
- Fedus et al., *Switch Transformer* (JMLR 2022) — sparsely activated experts.

## Contradiction / Hallucination Detection

- Manakul et al., *SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection* (EMNLP 2023).
- Laban et al., *SummaC: Re-Visiting NLI-based Models for Inconsistency Detection* (TACL 2022).
- Honovich et al., *TRUE: Re-evaluating Factual Consistency Evaluation* (NAACL 2022).
- DeBERTa-v3 / RoBERTa-large-MNLI — strong NLI backbones for contradiction detection.

## Evaluation & Benchmarks

- MMLU, HotpotQA, FEVER — multi-hop and fact verification benchmarks.
- HotpotQA (Yang et al., 2018) — multi-hop QA requiring cross-passage reasoning.
- FEVER (Thorne et al., 2018) — claim verification with evidence.

## Embeddings & Vector Stores

- Reimers & Gurevych, *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks* (EMNLP 2019).
- ChromaDB docs — https://docs.trychroma.com/
- FAISS (Johnson et al., 2019).

## RFCs in sample_docs

- RFC 793 (1981) → RFC 9293 (2022) — TCP evolution.
- RFC 2616 (1999) → RFC 9110 (2022) — HTTP semantics.
- RFC 5246 (TLS 1.2) → RFC 8446 (TLS 1.3).
