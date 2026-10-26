"""Installation-layout checks using the bundled runtime and an offline fake CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / 'scripts' / 'dispatch.py'
FIXTURE = Path(__file__).with_name('FAKE_GROK.py')


class PortableDispatch(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.task = self.root / 'task.md'
        self.task.write_text('Offline installation test.')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'grok'
        fake.write_text('#!' + sys.executable + '\n' + FIXTURE.read_text())
        fake.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env.get('PATH', ''),
                        BRIDGE_TEST_MARKER=str(self.root / 'calls'),
                        BRIDGE_TEST_CHILD=str(self.root / 'child'),
                        BRIDGE_TEST_SOURCE=str(self.source / 'context.py'))

    def run_dispatch(self, mode, *options):
        self.env['BRIDGE_TEST_MODE'] = mode
        command = [sys.executable, str(DISPATCH), str(self.task), '--source', str(self.source), *options]
        return subprocess.run(command, cwd=self.root, env=self.env, capture_output=True, text=True, timeout=20)

    def state(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        run = Path(completed.stdout.splitlines()[0])
        self.assertEqual(run.parent, ROOT / 'scripts' / 'runtime' / 'work')
        self.addCleanup(__import__('shutil').rmtree, run)
        return json.loads((run / 'status.json').read_text())

    def test_dry_run_uses_bundled_runtime_without_invoking_grok(self):
        completed = self.run_dispatch('success', '--expect', 'delivery.txt', '--dry-run')
        state = self.state(completed)
        self.assertEqual(state['status'], 'prepared')
        self.assertEqual(state['reasoning_effort'], 'low')
        self.assertEqual(state['budgets']['artifact_timeout'], 90)
        self.assertFalse((self.root / 'calls').exists())

    def test_edit_uses_bundled_runtime_from_another_directory(self):
        state = self.state(self.run_dispatch('success', '--expect', 'delivery.txt'))
        self.assertEqual(state['status'], 'needs_review')
        self.assertEqual(state['delivery_files'], ['delivery.txt'])
        self.assertEqual(state['source_drift'], [])

    def test_review_has_no_file_delivery_requirement(self):
        state = self.state(self.run_dispatch('review', '--profile', 'review'))
        self.assertEqual(state['status'], 'needs_review')
        self.assertEqual(state['delivery_state'], 'not_required_review_task')

    def test_missing_bridge_returns_codex_fallback(self):
        completed = self.run_dispatch('success', '--expect', 'delivery.txt', '--bridge', str(self.root / 'missing.py'))
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)['next_action'], 'codex_fallback')
        self.assertFalse((self.root / 'calls').exists())

    def test_edit_requires_explicit_outputs(self):
        completed = self.run_dispatch('success')
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('--expect', completed.stderr)
        self.assertFalse((self.root / 'calls').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
