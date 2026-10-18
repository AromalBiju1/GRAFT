# ADR 0002: The router uses fixed thresholds

- **Status:** Accepted

## Context

The router must decide how complex a question is so it can choose a retrieval depth and
which reasoning modules to run. The decision should be cheap, repeatable and easy to
test, and the system must be runnable without an LLM.

## Decision

The router computes a complexity score between 0 and 1 from weighted signals: question
length, clause count, number of distinct reasoning operations, and numeric,
multi-hop and contradiction hints. The score is compared against two fixed thresholds
defined in `config.py`:

- score below `router_threshold_simple` (**0.35**) -> `simple`
- score above `router_threshold_complex` (**0.65**) -> `complex`
- otherwise -> `moderate`

The thresholds are plain settings that can be overridden through the environment, and
the router rejects a configuration where the simple threshold is not below the complex
one. Confidence is reported as the distance from the nearest threshold.

## Consequences

- Routing is deterministic and dependency-free, so it is straightforward to unit test.
- No labelled data or training is needed for the MVP.
- Keyword and length heuristics can misclassify unusual phrasing.
- Thresholds are tuned by hand. Learned or trained thresholds are listed as a stretch
  goal in [scope.md](../scope.md). The public return shape of `classify` and `route`
  is meant to stay the same if a trained classifier replaces the heuristic.
- Changing a threshold affects benchmark results, so the owner of the benchmarking
  harness should be told (see [CONTRIBUTING.md](../../CONTRIBUTING.md)).
- Callers such as the evaluation harness can force a depth with
  `retrieval_depth_override` for ablation, without changing the classifier.