#!/usr/bin/env python3
"""Dispatch bounded Grok workers with streamed progress and finite recovery."""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid

from delivery_guard import DeliveryGuard

ROOT = Path(__file__).resolve().parent
NORMAL_STOPS = {"end_turn", "stop", "completed"}
RULES = (
    "You are an implementation worker reporting to Codex. Work only in the current "
    "task directory. Use file reading and editing tools to implement the supplied task. "
    "Do not invoke shell commands, delegate, access credentials, install dependencies, "
    "commit, push, publish, or modify files outside this directory. "
    "Do not ask for permissions. Report denied tools and blockers truthfully. "
    "Codex independently reviews and tests your delivery. Do not claim unrun tests passed. "
    "Finish with a short summary of changed files and remaining concerns.\n\n"
)


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path, help="UTF-8 task brief")
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--context", action="append", default=[], help="Relative context file; repeatable")
    parser.add_argument("--timeout", type=positive_seconds, default=1800, help="Overall seconds across all attempts")
    parser.add_argument("--attempt-timeout", type=positive_seconds, default=900)
    parser.add_argument("--first-response-timeout", type=positive_seconds, default=300)
    parser.add_argument("--idle-timeout", type=positive_seconds, default=300)
    parser.add_argument("--max-attempts", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--task-kind", choices=("edit", "review"), default="edit")
    parser.add_argument("--expect", action="append", default=[], help="Required changed output file; repeatable")
    parser.add_argument("--artifact-timeout", type=positive_seconds, default=180)
    parser.add_argument("--artifact-idle-timeout", type=positive_seconds, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_turns <= 0:
        parser.error("--max-turns must be positive")
    try:
        source = args.source.resolve(strict=True)
        if not source.is_dir():
            raise ValueError("--source must be a directory")
        args.source = source
        info = source.stat()
        args.source_identity = {"device": info.st_dev, "inode": info.st_ino}
        brief = args.task.resolve(strict=True).read_text(encoding="utf-8")
        files = []
        for name in args.context:
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or any(part.startswith(".") for part in rel.parts):
                raise ValueError("Context must be a non-hidden relative file without '..'")
            original = (source / rel).resolve(strict=True)
            if not original.is_relative_to(source) or not original.is_file():
                raise ValueError("Context must resolve to a file inside --source")
            files.append((rel, original))
        for name in args.expect:
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or not rel.parts or any(part.startswith(".") for part in rel.parts):
                raise ValueError("Expected output must be a non-hidden relative file without '..'")
        args.expect = sorted({Path(name).as_posix() for name in args.expect})
        if args.task_kind == "review" and args.expect:
            raise ValueError("Review tasks do not declare edited outputs")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return args, brief, files


def prepare(workspace, files):
    workspace.mkdir(parents=True)
    for rel, original in files:
        destination = workspace / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)


def source_drift(source, manifest):
    """Recheck only authorized source paths; never follow an escape outside source."""
    try:
        if source.resolve(strict=True) != source:
            return ["."] + [entry["path"] for entry in manifest["files"]]
        info = source.stat()
        if {"device": info.st_dev, "inode": info.st_ino} != manifest["source_root_identity"]:
            return ["."] + [entry["path"] for entry in manifest["files"]]
    except (OSError, RuntimeError):
        return ["."] + [entry["path"] for entry in manifest["files"]]
    changed = []
    for entry in manifest["files"]:
        try:
            current = (source / entry["path"]).resolve(strict=True)
            if not current.is_relative_to(source) or not current.is_file():
                changed.append(entry["path"])
            elif hashlib.sha256(current.read_bytes()).hexdigest() != entry["sha256"]:
                changed.append(entry["path"])
        except (OSError, RuntimeError):
            changed.append(entry["path"])
    return changed


def command(grok, workspace, prompt, max_turns, reasoning_effort="medium", task_kind="edit"):
    permissions = ["--permission-mode", "acceptEdits", "--allow", "Write", "--allow", "Edit", "--deny", "Bash", "--sandbox", "workspace"]
    if task_kind == "review":
        permissions = ["--permission-mode", "plan", "--deny", "Write", "--deny", "Edit", "--deny", "Bash", "--sandbox", "read-only"]
    return [grok, "--cwd", str(workspace), "--prompt-file", str(prompt),
            "--output-format", "streaming-json", "--reasoning-effort", reasoning_effort,
            *permissions, "--no-subagents",
            "--disable-web-search", "--max-turns", str(max_turns)]


class Stream:
    """Track protocol evidence; capabilities and local heartbeats are not progress."""

    def __init__(self):
        self.events = 0
        self.progress = 0
        self.first = None
        self.last = None
        self.kind = None
        self.text = []
        self.end = None
        self.errors = []
        self.invalid = 0
        self.tools = {}
        self.tool_calls = 0
        self.write_calls = 0

    def line(self, raw, now):
        if not raw.strip():
            return
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("expected event object")
        except (ValueError, UnicodeError):
            self.invalid += 1
            return {"type": "invalid_event", "bytes": len(raw)}
        self.events += 1
        kind = event.get("type")
        self.kind = kind if isinstance(kind, str) else "unknown"
        substantive = False
        if kind in ("text", "thought"):
            data = event.get("data")
            substantive = isinstance(data, str) and bool(data)
            if kind == "text" and substantive:
                self.text.append(data)
        elif kind in ("tool_call", "tool_call_update"):
            identifier = event.get("toolCallId")
            if isinstance(identifier, str) and identifier:
                # Ignore event kind: repeated identical call/update snapshots aren't new work.
                evidence = {key: event[key] for key in ("status", "rawInput", "rawOutput", "content", "locations") if key in event}
                fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
                substantive = self.tools.get(identifier) != fingerprint
                self.tools[identifier] = fingerprint
                if substantive and kind == "tool_call":
                    self.tool_calls += 1
                    if event.get("kind") == "edit" or str(event.get("toolName", "")).lower() in ("write", "edit", "write_file", "edit_file", "search_replace"):
                        self.write_calls += 1
        elif kind == "error":
            self.errors.append(event)
        elif kind == "end":
            if self.end is not None:
                self.invalid += 1
            self.end = event
        if substantive:
            self.progress += 1
            if self.first is None:
                self.first = now
            self.last = now
        summary = {"type": self.kind, "event_index": self.events}
        if kind in ("text", "thought"):
            summary["fragment_characters"] = len(event.get("data", "")) if isinstance(event.get("data"), str) else 0
        if kind in ("tool_call", "tool_call_update"):
            summary.update({key: event[key] for key in ("toolCallId", "toolName", "kind", "status") if key in event})
        if kind == "end":
            summary["stopReason"] = event.get("stopReason")
        return summary

    def snapshot(self, started, now, idle_limit):
        age = None if self.last is None else round(now - self.last, 3)
        observability = "awaiting_first_progress"
        if age is not None:
            observability = "silent_unknown" if age > min(30, idle_limit / 2) else "progress_observed"
        return {"event_count": self.events, "progress_event_count": self.progress,
                "model_activity_event_count": self.progress, "progress_basis": "model_activity_not_delivery",
                "tool_call_count": self.tool_calls, "write_tool_call_count": self.write_calls,
                "first_progress_seconds": None if self.first is None else round(self.first - started, 3),
                "last_progress_seconds": None if self.last is None else round(self.last - started, 3),
                "last_progress_age_seconds": age, "last_event_kind": self.kind,
                "observability": observability, "invalid_event_count": self.invalid}

    def response(self):
        result = dict(self.end or {})
        result["text"] = "".join(self.text)
        if self.errors:
            result["error"] = self.errors
        return result

    def failure(self, process_code):
        if self.errors or (self.end and (self.end.get("error") or self.end.get("is_error"))):
            return "worker_error"
        if self.invalid:
            return "invalid_response"
        if process_code != 0:
            return "process_failed"
        if self.end is None:
            return "incomplete_response"
        if self.end.get("stopReason") not in NORMAL_STOPS:
            return "incomplete_response"
        return None


def stop_process_group(process):
    # Only the dedicated group created for this invocation is signalled.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # The leader may exit promptly while a descendant ignores SIGTERM.
    # Reap/kill the whole dedicated group rather than testing only the leader.
    deadline = time.monotonic() + 0.2
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(0.02)
    process.wait()


def deadline_reason(args, stream, started, now, overall_deadline):
    if now >= overall_deadline:
        return "total_timeout"
    if now - started >= args.attempt_timeout:
        return "attempt_timeout"
    if stream.first is None and now - started >= args.first_response_timeout:
        return "first_response_timeout"
    if stream.last is not None and now - stream.last >= args.idle_timeout:
        return "idle_timeout"
    return None


def classify(reason, response, stderr_path):
    if reason in ("artifact_timeout", "artifact_idle_timeout", "missing_delivery", "unexpected_outputs", "output_boundary_violation"):
        return reason, False, False
    diagnostics = json.dumps(response.get("error", ""), ensure_ascii=False).casefold()
    diagnostics += "\n" + stderr_path.read_text(encoding="utf-8", errors="replace")[-16000:].casefold()
    if "permission required:" in diagnostics or "tool denied" in diagnostics:
        return "permission_required", False, True
    if any(term in diagnostics for term in ("not logged in", "please log in", "authentication required", "requires authentication", "unauthorized", "authentication failed", "expired token", "invalid token")):
        return "login_required", False, True
    if any(term in diagnostics for term in ("quota exhausted", "quota exceeded", "usage limit reached", "insufficient credits")):
        return "quota_exhausted", False, True
    if response.get("stopReason") in ("max_turns", "max_tokens"):
        return "work_limit", False, False
    return reason, reason != "total_timeout", False


def run_attempt(args, cmd, directory, env, overall_deadline, update):
    stream = Stream()
    started = time.monotonic()
    guard = DeliveryGuard(directory / "workspace", args.expect, args.task_kind, started,
                          args.artifact_timeout, args.artifact_idle_timeout)
    process = None
    reason = None
    pending = b""
    last_save = 0
    last_print = started
    process_code = None
    attempt = {"workspace": str(directory / "workspace"), "command": cmd,
               "events_path": str(directory / "events.jsonl"),
               "stderr_path": str(directory / "stderr.log"),
               "timeline_path": str(directory / "timeline.jsonl"),
               "response_path": str(directory / "attempt-response.json"), "status": "running"}
    with (directory / "events.jsonl").open("wb") as events, (directory / "stderr.log").open("wb") as err, (directory / "timeline.jsonl").open("w", encoding="utf-8") as timeline:
        def record(entry):
            if entry is not None:
                entry.update(received_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             elapsed_seconds=round(time.monotonic() - started, 3))
                timeline.write(json.dumps(entry, ensure_ascii=False) + "\n")
                timeline.flush()

        try:
            process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=err, env=env, start_new_session=True)
            attempt["pid"] = process.pid
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                eof = False
                while True:
                    for key, _ in selector.select(timeout=0.05):
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            eof = True
                            continue
                        events.write(chunk)
                        events.flush()
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            record(stream.line(line, time.monotonic()))
                        if len(pending) > 8 * 1024 * 1024:
                            reason = "invalid_response"
                            break
                    now = time.monotonic()
                    if eof and pending:
                        record(stream.line(pending, now))
                        pending = b""
                    process_code = process.poll()
                    for change in guard.observe(now, force=process_code is not None and eof):
                        record(dict(type="artifact_change", **change))
                    if process_code is not None and eof:
                        break
                    if reason is None:
                        reason = deadline_reason(args, stream, started, now, overall_deadline) or guard.reason(now)
                    if reason is not None:
                        break
                    if now - last_save >= 0.5:
                        attempt.update(stream.snapshot(started, now, args.idle_timeout), **guard.snapshot(now), elapsed_seconds=round(now - started, 3))
                        update(attempt)
                        last_save = now
                    if stream.progress and now - last_print >= 5:
                        # Deliberately omit raw text, thinking and tool payloads.
                        print(json.dumps({"status": "running", "model_activity_event_count": stream.progress,
                                          "delivery_state": guard.snapshot(now)["delivery_state"],
                                          "artifact_change_count": guard.change_count,
                                          "last_event_kind": stream.kind,
                                          "elapsed_seconds": round(now - started, 1)}), flush=True)
                        last_print = now
        except KeyboardInterrupt:
            reason = "interrupted"
        except OSError as exc:
            reason = "launch_failed"
            attempt["error"] = str(exc)
        finally:
            if process is not None:
                if reason is not None:
                    stop_process_group(process)
                elif process.poll() is None:
                    stop_process_group(process)
                process_code = process.returncode
                if process.stdout is not None:
                    process.stdout.close()
    guard.observe(time.monotonic(), force=True)
    reason = reason or stream.failure(process_code) or guard.completion_failure()
    response = stream.response()
    if reason:
        response.setdefault("error", reason)
    # The root response.json selects the final attempt. Keep this immutable
    # per-attempt response separately so retries do not erase earlier evidence.
    write_json(directory / "attempt-response.json", response)
    now = time.monotonic()
    failure_class, retryable, requires_action = (None, False, False)
    if reason and reason != "interrupted":
        failure_class, retryable, requires_action = classify(reason, response, directory / "stderr.log")
    attempt.update(stream.snapshot(started, now, args.idle_timeout), **guard.snapshot(now),
                   status="needs_review" if not reason else ("interrupted" if reason == "interrupted" else "failed"),
                   reason=reason, failure_class=failure_class, retryable=retryable,
                   requires_user_action=requires_action, process_exit_code=process_code,
                   elapsed_seconds=round(now - started, 3), stop_reason=response.get("stopReason"))
    return attempt, response


def main():
    args, brief, files = arguments()
    grok = shutil.which("grok") or str(Path.home() / ".grok/bin/grok")
    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    run = ROOT / "work" / run_id
    # Keep the exact dispatched bytes outside the worker sandbox, independently
    # of a concurrently changing target checkout. Retries use the same baseline.
    prepare(run / "baseline", files)
    frozen_files = [(rel, run / "baseline" / rel) for rel, _ in files]
    prepare(run / "workspace", frozen_files)
    manifest = {"source_root": str(args.source), "source_root_identity": args.source_identity, "files": [
        {"path": rel.as_posix(), "source_path": str(original), "baseline_path": str(run / "baseline" / rel),
         "sha256": hashlib.sha256((run / "baseline" / rel).read_bytes()).hexdigest()}
        for rel, original in files]}
    write_json(run / "source.json", manifest)
    prompt = run / "task.md"
    contract = "Task kind: " + args.task_kind + ". "
    if args.task_kind == "edit":
        contract += "Make the smallest reviewable working edit early; do not spend the whole budget only planning. Prose and reasoning are not a file delivery. "
        if args.expect:
            contract += "Only change these required output files: " + ", ".join(args.expect) + ". "
    else:
        contract += "Read and review only. Do not edit files. "
    prompt.write_text(RULES + contract + "\n\n" + brief, encoding="utf-8")
    first_cmd = command(grok, run / "workspace", prompt, args.max_turns, args.reasoning_effort, args.task_kind)
    state = {"id": run_id, "status": "prepared", "workspace": str(run / "workspace"),
             "context": [str(rel) for rel, _ in files], "command": first_cmd,
             "task_kind": args.task_kind, "expected_outputs": args.expect, "reasoning_effort": args.reasoning_effort,
             "source_manifest_path": str(run / "source.json"), "baseline_dir": str(run / "baseline"),
             "budgets": {key: getattr(args, key) for key in ("timeout", "attempt_timeout", "first_response_timeout", "idle_timeout", "artifact_timeout", "artifact_idle_timeout", "max_attempts")},
             "attempts": [], "next_action": "dispatch", "requires_user_action": False}
    started = time.monotonic()
    overall_deadline = started + args.timeout

    def save():
        state["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(run / "status.json", state)

    def update(attempt):
        state["attempts"][-1].update(attempt)
        for key in ("workspace", "command", "event_count", "progress_event_count", "model_activity_event_count", "progress_basis", "first_progress_seconds",
                    "last_progress_seconds", "last_progress_age_seconds", "last_event_kind", "observability", "tool_call_count", "write_tool_call_count",
                    "delivery_state", "artifact_change_count", "first_artifact_seconds", "last_artifact_seconds", "last_artifact_age_seconds",
                    "changed_files", "delivery_files", "missing_expected_files", "unexpected_outputs", "hidden_output_changes", "output_symlinks"):
            if key in attempt:
                state[key] = attempt[key]
        save()

    save()
    print(str(run), flush=True)
    if args.dry_run:
        return 0
    if not Path(grok).is_file():
        state.update(status="fallback_required", reason="missing_cli", next_action="codex_fallback", exit_code=1)
        write_json(run / "response.json", {"error": "missing_cli"})
        save()
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 1
    env = os.environ.copy()
    for key in ("XAI_API_KEY", "GROK_API_KEY", "GROK_DEPLOYMENT_KEY"):
        env.pop(key, None)
    code = 1
    try:
        for number in range(1, args.max_attempts + 1):
            if time.monotonic() >= overall_deadline:
                state.update(reason="total_timeout", status="fallback_required", next_action="codex_fallback")
                break
            directory = run if number == 1 else run / "attempts" / f"{number:02d}"
            if number > 1:
                prepare(directory / "workspace", frozen_files)
            cmd = command(grok, directory / "workspace", prompt, args.max_turns, args.reasoning_effort, args.task_kind)
            state["status"] = "running"
            state["attempts"].append({"number": number})
            save()
            attempt, response = run_attempt(args, cmd, directory, env, overall_deadline, update)
            update(attempt)
            write_json(run / "response.json", response)
            state.update(reason=attempt["reason"], failure_class=attempt["failure_class"],
                         stop_reason=attempt["stop_reason"], requires_user_action=attempt["requires_user_action"])
            if attempt["reason"] in ("artifact_timeout", "artifact_idle_timeout", "missing_delivery"):
                state["recovery_hint"] = "split_scope_or_codex_fallback; do not blindly repeat the same brief"
            if attempt["status"] == "needs_review":
                state.update(status="needs_review", next_action="review_delivery")
                code = 0
                break
            if attempt["status"] == "interrupted":
                state.update(status="interrupted", next_action="stop")
                code = 130
                break
            if not attempt["retryable"] or number == args.max_attempts:
                state.update(status="fallback_required", next_action="codex_fallback")
                break
            state.update(status="retrying", next_action="retry_fresh_workspace")
            save()
    except KeyboardInterrupt:
        state.update(status="interrupted", reason="interrupted", next_action="stop")
        code = 130
    except OSError as exc:
        state.update(status="fallback_required", reason="local_io_error", error=str(exc), next_action="codex_fallback")
    state["exit_code"] = code
    state["source_drift"] = source_drift(args.source, manifest)
    state["source_root_drift"] = "." in state["source_drift"]
    state["integration_status"] = "requires_independent_review_and_source_recheck"
    if not (run / "response.json").exists():
        write_json(run / "response.json", {"error": state.get("reason", "incomplete_response")})
    save()
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
