# Grok Supervised Workflow

A **Codex skill** for delegating bounded implementation tasks to the official Grok CLI while keeping Codex responsible for review, testing, recovery, and integration.

**Model activity is not delivery progress.** A worker can stream thousands of reasoning fragments without changing a file. This skill checks actual output bytes and uses independent artifact deadlines instead of treating every token as development progress.

[中文说明](README.zh-CN.md) · [Skill instructions](SKILL.md) · [Design references](references/decisions.md) · [MIT license](LICENSE)

## What it does

- Sends a small task packet and only selected context into a fresh workspace.
- Separates model activity, tool activity, and real file changes.
- Requires declared output files to change before an edit task can enter review.
- Limits first response, model silence, first artifact, artifact silence, attempts, and overall runtime independently.
- Preserves original context bytes and hashes for review and concurrent-source checks.
- Rejects unexpected visible/hidden outputs and symlinks; records hidden-path metadata without reading hidden contents.
- Returns a clear fallback result when recovery is exhausted. The coordinating Codex decides whether to shrink the task or finish directly.

A normal end and a successful process exit mean **needs review**, never automatic acceptance. The coordinator still reviews the complete diff, runs relevant checks, and preserves concurrent edits when integrating.

## Requirements

- macOS or Linux with Python 3.9+. Native Windows is not supported.
- The [official Grok CLI](https://docs.x.ai/build/cli/reference), installed and authenticated through its normal login flow.
- An account entitled to the selected Grok service/model. Check `grok inspect` to confirm the configuration you intend to use.

The runtime and tests use Python's standard library. The skill does not provision API keys, buy credits, or change global Grok permissions. Access, subscription limits, and billing remain controlled by the provider.

Development was tested with Grok CLI 1.0.67. The adapter expects its native `streaming-json` protocol; a CLI protocol change may require an update. This is an independent community project, not an official OpenAI or xAI product.

## Install in Codex

Clone this repository into the personal skills directory:

```sh
mkdir -p ~/.agents/skills
git clone https://github.com/hiroekiliku-hash/grok-supervised-workflow.git ~/.agents/skills/grok-supervised-workflow
```

If that directory already exists, inspect and back up your local version first rather than overwriting it. The repository includes the runtime, so no separate checkout or personal absolute path is required.

Use `$grok-supervised-workflow` in a Codex task, or let Codex select it for suitable delegation. [Current Codex documentation](https://learn.chatgpt.com/docs/build-skills) uses `~/.agents/skills`; older installations may use `~/.codex/skills` instead. Use the directory your Codex version discovers, and restart Codex if the skill does not appear. This package does not modify your global `AGENTS.md` automatically.

## Try it

From the repository root, prepare a task without calling the model:

```sh
python3 scripts/dispatch.py examples/task.md --source . --expect solution.py --dry-run
```

Remove `--dry-run` for a real invocation using your existing Grok login. That consumes provider usage and does not guarantee a correct result.

For an existing project:

```sh
python3 scripts/dispatch.py /path/to/task.md \
  --source /path/to/project \
  --context src/module.py \
  --expect src/module.py
```

Each `--expect` names a **required changed file**, not a context-only file. Repeat `--context` and `--expect` as needed. New-file tasks omit `--context`. Task text is read from a UTF-8 file and never interpolated into a shell command.

For a read-only opinion, use `--profile review` without `--expect`. Review mode uses plan permissions, a read-only sandbox, and denies Write, Edit, and Bash. Edit mode keeps the workspace sandbox, per-invocation editing permissions, and a Bash deny.

## Profiles

| Profile | Overall / attempt | First response / model idle | First artifact / artifact idle | Effort / attempts |
|---|---|---|---|---|
| `focused` (default) | 360 / 180 s | 120 / 90 s | 90 / 90 s | low / 1 |
| `standard` | 900 / 600 s | 180 / 180 s | 180 / 180 s | medium / 2 |
| `review` | 300 / 240 s | 120 / 120 s | not required | low / 1 |

These are configurable starting budgets, not universal deadlines. Run `python3 scripts/dispatch.py --help` for overrides.

For a thought-only stall, do not repeat the same broad prompt with a larger timeout. Make at most one materially smaller follow-up when useful and within budget, or let Codex finish. This decision belongs to the coordinator; the bridge does not spawn another Codex process.

## Inspect a run

The first output line is its run directory, under the ignored `scripts/runtime/work/` directory. Run records can contain copied project code and raw model output; review them before sharing.

- `status.json`: atomic live state, counters, outcome, selected workspace, and source drift.
- `baseline/` and `source.json`: captured input bytes, hashes, source paths, and root identity.
- `timeline.jsonl`: locally timestamped event types and artifact observations, without raw reasoning or tool arguments.
- `events.jsonl`: raw local protocol audit stream.
- `attempt-response.json`: preserved result for each attempt; root `response.json` selects the latest result.
- `attempts/02/`: an isolated retry workspace. Retries start from the captured baseline, not failed partial output.

Important outcomes:

| State | Meaning |
|---|---|
| `prepared` | No model invocation, including dry runs |
| `needs_review` | Protocol and delivery gates passed; coordinator review still required |
| `fallback_required` | Current Codex should split, simplify, or implement directly |
| `interrupted` | Task-owned invocation was stopped; no automatic repeat |

Legacy `progress_event_count` means model/tool activity, not delivery. Inspect `artifact_change_count` and `delivery_files` for artifact evidence. A local heartbeat does not prove remote computation, and silence does not prove permanent service failure.

The workspaces isolate deliveries; they are not a security boundary against an adversarial worker. Pass only authorized, non-secret context and keep normal CLI security controls. A file change is not proof of business correctness.

## Test without a Grok account

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Tests use offline fake workers and temporary files, including subprocess cleanup, streaming normalization, thought-only stalls, unchanged-file touches, declared outputs, hidden paths, immutable baselines, concurrent-source drift, and bundled installation layout. CI does not make model calls or require provider credentials.

The portable package was also smoke-tested on 2026-10-26 through a real authenticated Grok invocation: the example function arrived in about 12 seconds and passed 18,084 independently checked integer cases. Earlier development included real, isolated Grok runs on bounded edits, followed by independent behavior checks. Those observations are not a benchmark guarantee; private source, raw runs, and local account data are not included here.

## Contributing

Keep changes focused, include meaningful regression coverage, and run the offline suite. Do not commit run directories, copied project context, credentials, or raw model traces. Report security issues without posting secrets or private source.

See [task-packet guidance](references/task-packet.md) for the intended delegation shape. Third-party workflows were studied as design references; their code was not bundled or copied.
