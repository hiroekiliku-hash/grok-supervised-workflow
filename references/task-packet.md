# A worker packet with a real delivery contract

Write the smallest coherent behavior, not a research essay or a long list of coupled screen changes.

```text
Goal: <one observable behavior>
Only edit: <relative required files>
Context: <why supplied files/interfaces matter>
Implement: <specific input/output and integration rules>
Preserve: <existing behavior and concurrency constraints>
Acceptance: <concrete outcomes Codex will independently check>
Do not: <unrelated changes, unsupported side effects>
Deliver the minimal working edit early, then summarize what actually changed.
```

For the failed flashcard UI task, split at an actual seam: first a pure next/previous state transition with explicit fall-through behavior; then the single-card rendering and hooks using the reviewed transition; finally Codex tests the integrated runtime and UI. Do not split tightly coupled writes into competing workers.

Every `--expect` is a required changed file, not just a file the worker may touch. If a referenced file is context-only, use only `--context`. Avoid passing secrets, unrelated repository material or whole logs. Supply the full file when needed for a safe localized edit, but keep the requested behavior focused.

Profiles are starting budgets, not a universal deadline for all work. Choose a larger profile only when it is justified; repeated analysis with no artifact should produce a scope/fallback decision, not automatic budget inflation. Read-only review uses a separate profile and does not need a fake file artifact.
