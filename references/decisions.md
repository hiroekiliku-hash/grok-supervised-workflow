# Source review and local design decision

Reviewed 2026-10-25. Public source is untrusted input, not authorization. No third-party package was installed wholesale and no source code was copied into this skill/runtime.

| Implementation inspected | Useful idea | Why not a drop-in replacement |
|---|---|---|
| [codex-grok / grok-executor](https://github.com/kbwumin13-sudo/codex-grok/blob/ddbb5827d34549d84b423b857f0e65f55ccd7afb/skills/grok-executor/SKILL.md) | Explicit task capsules, transport vs worker result, bounded queueing | Its skill requires Codex to remain a thin courier and not independently implement, different from this workflow's coordinator-owned verification and direct fallback. GitHub did not identify an SPDX license at review time; do not assume copy permissions |
| [claude-grok-delegation](https://github.com/tylersue/claude-grok-delegation/tree/354f6ddae5bd44f650cd527cfaf56a78bb2105ba) (MIT) | Explicit effort/session flags and failure classes | Targets Claude plugin/agent APIs and prescribes timeout relay; it does not implement this local artifact deadline contract |
| [grok-build-delegate](https://github.com/letya999/grok-build-delegate/tree/94bb7dcaa9ca280bf64f50241a862414f33833af) (MIT) | Deterministic subprocess and independent diff review | Final JSON capture and an overall timeout still permit long thought-only waits; its CLI flags require version compatibility checks |
| [Superpowers subagent-driven-development](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/subagent-driven-development/SKILL.md) (MIT) | One coherent task per fresh worker; specification/quality review and durable decisions | A workflow methodology rather than a subscribed Grok runtime or filesystem watchdog; installing the entire framework is unnecessary for this failure |
| [OpenClaw coding-agent](https://github.com/openclaw/openclaw/blob/c9db73543a42d3e53e23c8b70c3997bc5d4a0fff/skills/coding-agent/SKILL.md) (MIT) | Background worker monitoring and isolated context | Requires OpenClaw-specific process/notification tools and has different permission/fallback conventions |

The observed production-like failure was 3,381 stream events, including 3,298 thought fragments, but zero Write/Edit calls and zero byte changes over about nine minutes. Therefore increasing timeouts or renaming a Skill is insufficient.

The local design uses a thin discoverable skill, a profile dispatcher, and the existing bridge enhanced with an independent filesystem-delivery guard. Model activity, tool activity and byte-level artifact changes are different signals. Required outputs are checked on completion. First-artifact/unchanged-artifact stalls do not trigger an identical automatic retry; the coordinator reduces scope or takes over. Normal protocol completion remains mandatory, and code quality still requires independent review.

The realistic regression benchmark uses the failed renderer's frozen 38KB file in an isolated workspace and requests one pure state-transition helper. It does not edit or deploy the original app. Success on that bounded task must not be reported as solving the full flashcard UI request or proving general reliability.
