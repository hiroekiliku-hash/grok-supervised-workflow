#!/usr/bin/env python3
"""Apply a bounded delivery profile to the bundled Grok bridge."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

BRIDGE = Path(__file__).resolve().parent / 'runtime' / 'bridge.py'
PROFILES = {
    'focused': dict(timeout=360, attempt_timeout=180, first_response_timeout=120,
                    idle_timeout=90, artifact_timeout=90, artifact_idle_timeout=90,
                    max_attempts=1, max_turns=8, reasoning_effort='low', task_kind='edit'),
    'standard': dict(timeout=900, attempt_timeout=600, first_response_timeout=180,
                     idle_timeout=180, artifact_timeout=180, artifact_idle_timeout=180,
                     max_attempts=2, max_turns=12, reasoning_effort='medium', task_kind='edit'),
    'review': dict(timeout=300, attempt_timeout=240, first_response_timeout=120,
                   idle_timeout=120, artifact_timeout=180, artifact_idle_timeout=180,
                   max_attempts=1, max_turns=8, reasoning_effort='low', task_kind='review'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--context', action='append', default=[])
    parser.add_argument('--expect', action='append', default=[])
    parser.add_argument('--profile', choices=PROFILES, default='focused')
    parser.add_argument('--bridge', type=Path, default=BRIDGE)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--reasoning-effort', choices=('low', 'medium', 'high'))
    for key in ('timeout', 'attempt-timeout', 'first-response-timeout', 'idle-timeout', 'artifact-timeout', 'artifact-idle-timeout'):
        parser.add_argument('--' + key, type=float)
    args = parser.parse_args()
    if args.profile == 'review' and args.expect:
        parser.error('Read-only review does not declare edited outputs')
    if args.profile != 'review' and not args.expect:
        parser.error('Implementation tasks require at least one --expect output')
    if not args.bridge.is_file():
        print(json.dumps({'status': 'fallback_required', 'reason': 'missing_bridge',
                          'next_action': 'codex_fallback', 'requires_user_action': False}))
        return 1
    profile = dict(PROFILES[args.profile])
    for key in profile:
        override = getattr(args, key, None)
        if override is not None:
            profile[key] = override
    cmd = [sys.executable, str(args.bridge), str(args.task), '--source', str(args.source)]
    for path in args.context:
        cmd.extend(('--context', path))
    for path in args.expect:
        cmd.extend(('--expect', path))
    for key, value in profile.items():
        cmd.extend(('--' + key.replace('_', '-'), str(value)))
    if args.dry_run:
        cmd.append('--dry-run')
    # Bridge owns the dedicated Grok group and handles deadlines. Do not impose
    # a second blind subprocess timeout that could orphan its worker.
    return subprocess.call(cmd)


if __name__ == '__main__':
    sys.exit(main())
