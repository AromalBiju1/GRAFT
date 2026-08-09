# Contributing to GRAFT

This is a 4-person final year project repo. These rules exist to stop integration
week from becoming a disaster — follow them even though it's "just a college project."

## Branching

- `main` — always working. Nothing broken gets merged here.
- `dev` — integration branch. Feature branches merge here first.
- `feature/<module>-<short-description>` — e.g. `feature/router-complexity-classifier`

Never commit directly to `main`. Never commit directly to `dev` without a PR, even
if it feels faster — the whole point is catching integration breaks early.

## Module Ownership

Each specialist module, the router, the indexing pipeline, and the benchmarking
harness has one primary owner (see README). You can work outside your module, but
the owner reviews and approves changes to their area.

| Area | Owner |
|---|---|
| Indexing / tree construction | |
| Router / gating | |
| Specialist modules | |
| Benchmarking harness | |

## Commit Messages

Format: `<module>: <what changed>`

Examples:
- `router: add rule-based complexity threshold`
- `contradiction: integrate NLI model for claim comparison`
- `benchmark: add latency + modules-fired metrics`

Avoid vague messages like `fix stuff` or `update`. Future-you debugging integration
issues at 2am will thank present-you.

## Before Opening a Pull Request

1. Your code runs locally without errors.
2. You've added/updated a basic test or smoke test for what you changed — even a
   simple assertion is better than nothing. Shallow, untested code is not acceptable
   for module handoffs.
2. You've pulled the latest `dev` and resolved conflicts locally, not in the PR.
3. You've filled out the PR template completely — no empty sections.

## Code Review

- Every PR needs at least **one approval** from someone other than the author before
  merging into `dev`.
- Reviewers check: does it actually run, does it match the interface the other
  modules expect (input/output format agreed on beforehand), is there at least a
  basic test.
- If you're the module owner and someone else touches your area, you're expected to
  review it — don't let PRs sit unreviewed for more than 2–3 days.

## Interface Contracts

Because four people are building four separate pieces that must connect, **agree on
input/output formats before writing the module**, not after. Document this in
`docs/interfaces.md`. If you change a module's input/output shape, flag it to
whoever depends on it before merging — don't let them find out when integration
breaks.

## Integration Checkpoints

Per the project timeline, there are scheduled integration checkpoints (see
`docs/scope.md`). Before each checkpoint:
- All in-progress PRs targeting that checkpoint should be merged to `dev` at least
  2 days prior, not the night before.
- Run the full pipeline end-to-end locally before the checkpoint meeting, not during it.

## Benchmarking Changes

Any change that could affect accuracy, latency, or retrieval behavior (tree
construction, router thresholds, module logic) should be flagged to whoever owns
the benchmarking harness — results need to reflect the current system, not a stale
version.

## Reporting Issues

Use GitHub Issues for bugs, blockers, or design questions that affect more than
your own module. Tag the relevant owner. Don't let integration-blocking issues sit
undiscussed in a WhatsApp thread only.

## Scope Discipline

MVP scope is locked (see `docs/scope.md`). Stretch goals (cross-query pipelining,
learned gating thresholds, write-back persistence) only get worked on after the
MVP is integrated and benchmarked. If you're unsure whether something is in scope,
ask before building it.
