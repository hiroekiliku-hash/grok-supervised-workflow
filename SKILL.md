---
name: grok-supervised-workflow
description: Coordinate bounded local implementation through the subscribed Grok CLI, monitor actual file delivery, independently review and test, and recover with Codex when delegation stalls. Use for substantial coding subtasks selected for Grok; not ordinary conversation or tasks requiring no delegated work.
---

# Supervise Grok delivery

The user gives work to Codex, not to a courier. Codex owns completion and may implement directly when a worker stalls or fails review. Routine wait/retry/split decisions do not require user input. Preserve normal authorization, sandbox, credential and publication boundaries.

This skill uses the officially installed Grok CLI and its existing login. The runtime is bundled with the skill. No other skill, MCP server or third-party Python dependency is required. Subscription eligibility and usage limits remain controlled by Grok; no API key is provisioned or purchased by this skill.

## Choose a reviewable task

Keep architecture and scope decisions in Codex. Dispatch one coherent behavior with concrete required files and acceptance criteria. A large file can be context for a small edit; a complete coupled UI redesign is usually several tasks. See [task packet](references/task-packet.md) when preparing a new brief.

Prefer `focused` for a well-understood edit (low reasoning effort). Use `standard` only when the bounded task needs more analysis. Use `review` for a read-only opinion; it never requires edits. Do not delegate ordinary conversation or tiny direct actions just to satisfy a routing preference.

The official CLI must already be installed and authenticated. If it is unavailable, continue with Codex where possible; request human repair only when genuinely necessary. Do not read/copy credential stores, buy credits, grant blanket approval or work around execution restrictions.

## Dispatch

Create the brief in the active task's agent/work directory. Pass only needed context and explicitly name each required changed output. The dispatcher supplies bounded profiles and invokes the maintained bridge without a shell:

```sh
python3 /path/to/grok-supervised-workflow/scripts/dispatch.py /absolute/brief.md --source /absolute/project --context src/module.js --expect src/module.js
```

New-file tasks omit `--context` and still supply `--expect`. For read-only work use `--profile review` without `--expect`. `--dry-run` prepares state only; it is never model-call evidence. `--help` lists profile overrides. Use `--bridge /path/to/bridge.py` only to select a different compatible runtime explicitly.

The bundled runtime is `scripts/runtime/bridge.py` relative to this skill. Read the repository README for compatibility and protocol limits. The dispatcher reports `codex_fallback` if the selected runtime is missing; do not improvise credential/proxy setup.

## Read evidence, not a success claim

The first output line identifies the run directory. Read `status.json`, selected `response.json`, and the latest attempt's paths. Preserve state across interruptions; do not blindly relaunch completed work.

- `model_activity_event_count` and the legacy `progress_event_count` mean received model/tool activity. Thinking, repeated prose, and read/search operations do not prove implementation progress.
- `artifact_change_count`, `delivery_files` and the byte hashes reflect actual workspace file changes. Touching an unchanged file or claiming a Write does not count. An artifact is still unverified code.
- `timeline.jsonl` timestamps each sanitized event and artifact observation without raw reasoning or tool payloads. Use it to diagnose ordering and delays; raw `events.jsonl` is local audit data, not user-facing progress.
- The first-artifact and artifact-idle deadlines are separate from connection/response silence and total/attempt caps. Model activity cannot extend them.
- `needs_review` requires a normal end, valid protocol, actual expected changed files and no unexpected outputs. It is not acceptance. A review task can finish without file changes.

## Recover autonomously within a finite budget

For `artifact_timeout`, `artifact_idle_timeout` or `missing_delivery`, do not rerun the same broad brief. Make at most one materially smaller follow-up if it removes the observed difficulty and fits the remaining task budget; otherwise implement the remainder in Codex. Preserve failed work for review, and stop only invocations owned by this task.

Transient transport failures may receive the bridge's bounded fresh-workspace retry. Repeated thought-only activity is a reason to change scope or take over, not proof of permanent service death and not grounds for unlimited waiting. Confirmed login/quota/access failures may need account repair, but Codex can still finish suitable work directly.

The bridge does not ask another model to split work or spawn Codex. These are the current coordinator's responsibilities. Record the decision and accepted run in the existing task state; do not narrate routine recovery or ask the user whether to continue waiting.

## Accept and integrate

Inspect the entire diff and every changed file against the immutable run `baseline/` and `source.json`, not a possibly changed live source checkout. The manifest records source-root identity and context hashes; `source_drift` identifies concurrent changes and `.` denotes a replaced/redirected root. Hidden output paths are inventoried without reading hidden contents and are not allowed to escape the declared output contract. Verify requirements and meaningful edge/error cases with the real project build/runtime checks appropriate to the change. Check architecture fit, maintainability, complexity, errors and relevant security/performance. Reject fake results, disabled checks, unrelated changes and brittle patches.

Recheck the target checkout before integrating, preserving concurrent edits. Never overwrite a newer source file with a worker's stale full-file copy. Fix or take over autonomously when needed. Attribute Grok versus Codex implementation truthfully; disclose material verification gaps. A small successful benchmark does not establish general production reliability.

For why existing public delegation skills were not installed wholesale, see [design and source review](references/decisions.md).
