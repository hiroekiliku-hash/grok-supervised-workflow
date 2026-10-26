"""Independent black-box checks; uses an offline fake CLI, never the real Grok service."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

BRIDGE = Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 and sys.argv[1].endswith('.py') else Path(__file__).resolve().parents[1] / 'scripts' / 'runtime' / 'bridge.py'
FIXTURE = Path(__file__).with_name('FAKE_GROK.py')


class BridgeContract(unittest.TestCase):
    def setUp(self):
        (BRIDGE.parent / 'work').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='bridge-contract-', dir=BRIDGE.parent / 'work')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.entry = self.root / 'bridge.py'
        shutil.copyfile(BRIDGE, self.entry)
        for name in ("delivery_guard.py", "artifact_scan.py"):
            shutil.copyfile(BRIDGE.with_name(name), self.root / name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'context.py').write_text('baseline')
        self.task = self.root / 'task.md'
        self.task.write_text('Offline fixture task only.')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.fake = self.bin / 'grok'
        self.fake.write_text('#!' + sys.executable + '\n' + FIXTURE.read_text())
        self.fake.chmod(0o755)
        self.marker = self.root / 'calls'
        self.child_marker = self.root / 'child-pid'

    def invocation(self, mode, *extra):
        env = os.environ.copy()
        env.update(PATH=str(self.bin) + os.pathsep + env.get('PATH', ''),
                   BRIDGE_TEST_MODE=mode, BRIDGE_TEST_MARKER=str(self.marker),
                   BRIDGE_TEST_CHILD=str(self.child_marker), BRIDGE_TEST_SOURCE=str(self.source/'context.py'),
                   XAI_API_KEY='test-sentinel', GROK_API_KEY='test-sentinel', GROK_DEPLOYMENT_KEY='test-sentinel')
        cmd = [sys.executable, str(self.entry), str(self.task), '--source', str(self.source),
               '--context', 'context.py', '--timeout', '12', '--attempt-timeout', '8',
               '--first-response-timeout', '5', '--idle-timeout', '1', '--max-attempts', '1', *extra]
        return cmd, env

    def run_case(self, mode, *extra):
        cmd, env = self.invocation(mode, *extra)
        completed = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=20)
        paths = list((self.root / 'work').glob('*/status.json'))
        self.assertEqual(len(paths), 1, completed.stderr + completed.stdout)
        state = json.loads(paths[0].read_text())
        return completed, state, paths[0].parent

    def calls(self):
        return int(self.marker.read_text()) if self.marker.exists() else 0

    def assert_fallback(self, completed, state, reason=None):
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(state['status'], 'fallback_required')
        self.assertEqual(state['next_action'], 'codex_fallback')
        if reason:
            self.assertEqual(state['reason'], reason, state)

    def test_success_normalizes_stream_and_keeps_privacy(self):
        c, s, run = self.run_case('success')
        self.assertEqual(c.returncode, 0, c.stderr + c.stdout)
        self.assertEqual(s['status'], 'needs_review')
        self.assertEqual(s['next_action'], 'review_delivery')
        response = json.loads((run / 'response.json').read_text())
        self.assertEqual(response['stopReason'], 'end_turn')
        self.assertEqual(response['text'], '完成🙂')
        self.assertNotIn('PRIVATE_THOUGHT_SENTINEL', response['text'])
        self.assertNotIn('PRIVATE_THOUGHT_SENTINEL', c.stdout)
        self.assertNotIn('完成🙂', c.stdout)
        self.assertEqual((Path(s['workspace']) / 'delivery.txt').read_text(), 'verified')
        self.assertEqual((self.source / 'context.py').read_text(), 'baseline')
        self.assertGreater(s.get('progress_event_count', s['attempts'][-1].get('progress_event_count', 0)), 0)
        self.assertGreater((run / 'events.jsonl').stat().st_size, 0)

    def test_live_snapshot_and_progress_prevents_premature_timeout(self):
        cmd, env = self.invocation('live', '--first-response-timeout', '1.5', '--idle-timeout', '1', '--attempt-timeout', '3', '--timeout', '5')
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        run = Path(p.stdout.readline().strip())
        seen = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and p.poll() is None:
            try:
                s = json.loads((run / 'status.json').read_text())
                count = s.get('progress_event_count', 0)
                if not count and s.get('attempts'): count = s['attempts'][-1].get('progress_event_count', 0)
                if s['status'] == 'running' and count > 0:
                    seen = True
                    self.assertGreater((run / 'events.jsonl').stat().st_size, 0)
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(.02)
        stdout, stderr = p.communicate(timeout=5)
        self.assertTrue(seen, stdout + stderr)
        self.assertEqual(p.returncode, 0, stderr)

    def test_heartbeat_does_not_reset_first_response_deadline(self):
        c, s, _ = self.run_case('heartbeat', '--first-response-timeout', '.16')
        self.assert_fallback(c, s, 'first_response_timeout')
        self.assertFalse(s.get('requires_user_action'))

    def test_usage_does_not_reset_idle_deadline(self):
        c, s, _ = self.run_case('idle', '--idle-timeout', '.16')
        self.assert_fallback(c, s, 'idle_timeout')

    def test_duplicate_tool_status_does_not_reset_idle_deadline(self):
        c, s, _ = self.run_case('same_tool', '--idle-timeout', '.16')
        self.assert_fallback(c, s, 'idle_timeout')

    def test_attempt_cap_with_continuous_progress(self):
        c, s, _ = self.run_case('hard', '--attempt-timeout', '.2')
        self.assert_fallback(c, s, 'attempt_timeout')

    def test_overall_cap_takes_precedence(self):
        c, s, _ = self.run_case('heartbeat', '--timeout', '.2', '--first-response-timeout', '5', '--attempt-timeout', '5')
        self.assert_fallback(c, s, 'total_timeout')
        self.assertLessEqual(self.calls(), 1)

    def test_fresh_workspace_bounded_automatic_retry(self):
        c, s, _ = self.run_case('retry', '--max-attempts', '2', '--first-response-timeout', '1.5', '--attempt-timeout', '3', '--timeout', '6')
        self.assertEqual(c.returncode, 0, c.stderr + c.stdout)
        self.assertEqual(self.calls(), 2)
        self.assertEqual(len(s['attempts']), 2)
        self.assertEqual((Path(s['workspace']) / 'delivery.txt').read_text(), 'fresh')
        self.assertNotEqual(s['attempts'][0]['workspace'], s['attempts'][1]['workspace'])
        first = json.loads(Path(s['attempts'][0]['response_path']).read_text())
        second = json.loads(Path(s['attempts'][1]['response_path']).read_text())
        self.assertIn('error', first)
        self.assertEqual(second['stopReason'], 'end_turn')
        self.assertNotEqual(s['attempts'][0]['response_path'], s['attempts'][1]['response_path'])
        self.assertEqual((self.source / 'context.py').read_text(), 'baseline')

    def test_permanent_blockers_do_not_loop(self):
        for mode in ('auth', 'quota', 'permission'):
            with self.subTest(mode=mode):
                # Each subcase gets an independent root and counter.
                c, s, _ = self.run_case(mode, '--max-attempts', '3')
                self.assert_fallback(c, s)
                self.assertTrue(s.get('requires_user_action'), s)
                self.assertEqual(self.calls(), 1)
                shutil.rmtree(self.root / 'work')
                self.marker.unlink()

    def test_nonfatal_permission_warning_is_accepted(self):
        c, s, _ = self.run_case('warnings')
        self.assertEqual(c.returncode, 0, c.stderr + c.stdout)
        self.assertEqual(s['status'], 'needs_review')

    def test_malformed_missing_and_error_results_never_accepted(self):
        for mode in ('invalid', 'no_end', 'error_end'):
            with self.subTest(mode=mode):
                c, s, _ = self.run_case(mode, '--max-attempts', '2')
                self.assert_fallback(c, s)
                self.assertLessEqual(self.calls(), 2)
                shutil.rmtree(self.root / 'work')
                self.marker.unlink()

    def test_turn_limit_does_not_repeat_same_task(self):
        c, s, _ = self.run_case('max_turns', '--max-attempts', '3')
        self.assert_fallback(c, s)
        self.assertEqual(self.calls(), 1)

    def test_dry_run_never_invokes_cli(self):
        c, s, _ = self.run_case('success', '--dry-run')
        self.assertEqual(c.returncode, 0, c.stderr + c.stdout)
        self.assertEqual(s['status'], 'prepared')
        self.assertEqual(self.calls(), 0)

    def test_invalid_bounds_rejected_without_invocation(self):
        for option, value in [('--timeout', '0'), ('--timeout', 'nan'), ('--idle-timeout', 'inf'), ('--first-response-timeout', '-1'), ('--max-attempts', '0'), ('--max-attempts', '4')]:
            with self.subTest(option=option, value=value):
                cmd, env = self.invocation('success', option, value)
                c = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=3)
                self.assertNotEqual(c.returncode, 0)
                self.assertEqual(self.calls(), 0)

    def test_context_escape_hidden_absolute_and_symlink_rejected(self):
        (self.root / 'outside.py').write_text('private')
        (self.source / 'link.py').symlink_to(self.root / 'outside.py')
        (self.source / '.hidden').write_text('private')
        for name in ('../outside.py', str(self.root / 'outside.py'), '.hidden', 'link.py'):
            with self.subTest(name=name):
                cmd, env = self.invocation('success', '--context', name)
                c = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=3)
                self.assertNotEqual(c.returncode, 0)
                self.assertEqual(self.calls(), 0)

    def test_timeout_and_interrupt_clean_up_child_group(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                cmd, env = self.invocation('child', '--idle-timeout', '3' if interrupted else '1.5', '--first-response-timeout', '3', '--attempt-timeout', '6', '--timeout', '8', '--max-attempts', '2' if interrupted else '1')
                p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not self.child_marker.exists() and time.monotonic() < deadline and p.poll() is None: time.sleep(.02)
                    self.assertTrue(self.child_marker.exists())
                    child_pid = int(self.child_marker.read_text())
                    if interrupted: p.send_signal(signal.SIGINT)
                    stdout, stderr = p.communicate(timeout=5)
                    if interrupted:
                        state = json.loads(next((self.root / 'work').glob('*/status.json')).read_text())
                        self.assertEqual(state['status'], 'interrupted', stdout + stderr)
                        self.assertEqual(self.calls(), 1)
                    dead = False
                    for _ in range(30):
                        stat = subprocess.run(['ps', '-o', 'stat=', '-p', str(child_pid)], capture_output=True, text=True).stdout.strip()
                        if not stat or stat.startswith('Z'): dead = True; break
                        time.sleep(.03)
                    self.assertTrue(dead, 'Fixture descendant left running')
                finally:
                    if p.poll() is None: p.kill(); p.wait()
                    if p.stdout: p.stdout.close()
                    if p.stderr: p.stderr.close()
                    if self.child_marker.exists():
                        try: os.kill(int(self.child_marker.read_text()), signal.SIGKILL)
                        except ProcessLookupError: pass
                shutil.rmtree(self.root / 'work')
                self.marker.unlink()
                self.child_marker.unlink()


    def test_thoughts_cannot_extend_artifact_deadline_or_trigger_blind_retry(self):
        c, s, run = self.run_case('thought_loop', '--artifact-timeout', '3', '--first-response-timeout', '5', '--max-attempts', '3', '--expect', 'delivery.txt')
        self.assert_fallback(c, s, 'artifact_timeout')
        self.assertEqual(self.calls(), 1)
        self.assertFalse(s['requires_user_action'])
        self.assertEqual(s['artifact_change_count'], 0)
        self.assertGreater(s['model_activity_event_count'], 0)
        self.assertEqual(s['delivery_state'], 'awaiting_artifact')
        timeline = [json.loads(line) for line in (run / 'timeline.jsonl').read_text().splitlines()]
        self.assertTrue(all('received_at' in e and 'elapsed_seconds' in e for e in timeline))
        self.assertNotIn('THOUGHT_CONTENT_NOT_FOR_TIMELINE', (run / 'timeline.jsonl').read_text())
        self.assertIn('split', s['recovery_hint'])

    def test_write_claim_and_touch_are_not_artifacts(self):
        for mode in ('write_claim', 'touch_only'):
            with self.subTest(mode=mode):
                c, s, _ = self.run_case(mode, '--artifact-timeout', '3', '--first-response-timeout', '5', '--expect', 'delivery.txt')
                self.assert_fallback(c, s, 'artifact_timeout')
                self.assertEqual(s['artifact_change_count'], 0)
                shutil.rmtree(self.root / 'work'); self.marker.unlink()

    def test_partial_artifact_then_thoughts_hits_artifact_idle_deadline(self):
        c, s, _ = self.run_case('partial_stall', '--artifact-idle-timeout', '.3', '--expect', 'delivery.txt')
        self.assert_fallback(c, s, 'artifact_idle_timeout')
        self.assertGreaterEqual(s['artifact_change_count'], 1)

    def test_successful_exit_without_required_file_is_rejected(self):
        c, s, _ = self.run_case('empty_success', '--expect', 'delivery.txt', '--max-attempts', '3')
        self.assert_fallback(c, s, 'missing_delivery')
        self.assertEqual(self.calls(), 1)

    def test_unexpected_output_is_rejected(self):
        c, s, _ = self.run_case('unexpected', '--expect', 'delivery.txt')
        self.assert_fallback(c, s, 'unexpected_outputs')
        self.assertEqual(s['unexpected_outputs'], ['rogue.txt'])

    def test_review_mode_does_not_require_edits(self):
        c, s, _ = self.run_case('review', '--task-kind', 'review')
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        self.assertEqual(s['delivery_state'], 'not_required_review_task')

    def test_invalid_expected_paths_are_rejected(self):
        for name in ('../outside.txt', '/absolute.txt', '.hidden'):
            cmd, env = self.invocation('success', '--expect', name)
            c = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=3)
            self.assertNotEqual(c.returncode, 0)
            self.assertEqual(self.calls(), 0)


    def test_hidden_outputs_and_hidden_symlinks_are_rejected(self):
        for mode in ('hidden_file', 'hidden_dir', 'hidden_link'):
            with self.subTest(mode=mode):
                c, s, _ = self.run_case(mode, '--expect', 'delivery.txt')
                self.assert_fallback(c, s, 'output_boundary_violation' if mode=='hidden_link' else 'unexpected_outputs')
                self.assertTrue(s['hidden_output_changes'])
                shutil.rmtree(self.root/'work');self.marker.unlink()

    def test_baseline_survives_source_drift_and_worker_overwrite(self):
        c, s, run = self.run_case('source_drift', '--expect', 'delivery.txt', '--max-attempts', '2', '--first-response-timeout', '5', '--attempt-timeout', '7', '--timeout', '16')
        self.assertEqual(c.returncode, 0, c.stdout+c.stderr)
        self.assertEqual((run/'baseline/context.py').read_text(), 'baseline')
        self.assertEqual((Path(s['workspace'])/'context.py').read_text(), 'baseline')
        self.assertEqual((self.source/'context.py').read_text(), 'concurrent edit')
        self.assertEqual(s['source_drift'], ['context.py'])
        manifest=json.loads((run/'source.json').read_text())
        self.assertEqual(manifest['files'][0]['path'], 'context.py')
        self.assertTrue(manifest['files'][0]['sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
