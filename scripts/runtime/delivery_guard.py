"""Observe delivery evidence separately from model/transport activity."""
import os
from pathlib import Path
import stat

from artifact_scan import changed_paths, file_snapshot


def boundary_inventory(workspace):
    """Observe hidden path metadata and all symlinks, never hidden contents."""
    hidden, links = {}, []
    root = Path(workspace)
    for parent, directories, files, directory_fd in os.fwalk(root, follow_symlinks=False):
        for name in directories + files:
            relative = (Path(parent) / name).relative_to(root)
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                links.append(relative.as_posix())
            if any(part.startswith('.') for part in relative.parts):
                hidden[relative.as_posix()] = ('directory',) if stat.S_ISDIR(info.st_mode) else (info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return hidden, sorted(links)


class DeliveryGuard:
    def __init__(self, workspace, expected, mode, started, first_limit, idle_limit):
        self.workspace = Path(workspace)
        self.expected = set(expected)
        self.mode = mode
        self.started = started
        self.first_limit = first_limit
        self.idle_limit = idle_limit
        self.interval = max(0.02, min(0.5, first_limit / 4, idle_limit / 4))
        self.baseline = file_snapshot(self.workspace)
        self.baseline_hidden, _ = boundary_inventory(self.workspace)
        self.hidden = self.baseline_hidden
        self.hidden_changes = []
        self.previous = self.baseline
        self.current = self.baseline
        self.last_scan = started - self.interval
        self.first = None
        self.last = None
        self.change_count = 0
        self.delivery = {}
        self.symlinks = []
        self.expired = None

    def observe(self, now, force=False):
        if not force and now - self.last_scan < self.interval:
            return []
        # An artifact observed after its deadline cannot retroactively rescue
        # an expired attempt, including when EOF and a file arrive together.
        self.expired = self.expired or self.reason(now)
        self.last_scan = now
        hidden, self.symlinks = boundary_inventory(self.workspace)
        hidden_observations = changed_paths(self.hidden, hidden)
        self.hidden_changes = changed_paths(self.baseline_hidden, hidden)
        self.hidden = hidden
        current = file_snapshot(self.workspace)
        changed = changed_paths(self.previous, current)
        eligible = {name: digest for name, digest in current.items()
                    if self.baseline.get(name) != digest and (not self.expected or name in self.expected)}
        if self.mode != "edit":
            eligible = {}
        if eligible and eligible != self.delivery:
            self.change_count += 1
            if self.first is None:
                self.first = now
            self.last = now
        self.delivery = eligible
        self.current = current
        self.previous = current
        return ([{"path": name, "change": "deleted" if name not in current else "content_changed",
                  "sha256": current.get(name)} for name in changed] +
                [{"path": name, "change": "hidden_metadata_changed", "sha256": None} for name in hidden_observations])

    def reason(self, now):
        if self.expired:
            return self.expired
        if self.mode != "edit":
            return None
        if self.symlinks:
            return "output_boundary_violation"
        if self.first is None and now - self.started >= self.first_limit:
            return "artifact_timeout"
        if self.last is not None and now - self.last >= self.idle_limit:
            return "artifact_idle_timeout"
        return None

    def completion_failure(self):
        if self.expired:
            return self.expired
        if self.symlinks:
            return "output_boundary_violation"
        if self.hidden_changes:
            return "unexpected_outputs"
        changed = set(changed_paths(self.baseline, self.current))
        if self.mode != "edit":
            return "unexpected_outputs" if changed else None
        if self.expected and changed - self.expected:
            return "unexpected_outputs"
        if not self.delivery or (self.expected and self.expected - self.delivery.keys()):
            return "missing_delivery"
        return None

    def snapshot(self, now):
        changed = set(changed_paths(self.baseline, self.current))
        if self.mode != "edit":
            state = "not_required_review_task"
        elif self.delivery:
            state = "artifact_present_unverified"
        elif self.first is not None:
            state = "artifact_reverted_or_removed"
        else:
            state = "awaiting_artifact"
        return {"delivery_state": state, "artifact_change_count": self.change_count,
                "first_artifact_seconds": None if self.first is None else round(self.first - self.started, 3),
                "last_artifact_seconds": None if self.last is None else round(self.last - self.started, 3),
                "last_artifact_age_seconds": None if self.last is None else round(now - self.last, 3),
                "changed_files": sorted(changed), "delivery_files": sorted(self.delivery),
                "missing_expected_files": sorted(self.expected - self.delivery.keys()),
                "unexpected_outputs": sorted((changed - self.expected if self.expected else set()) | set(self.hidden_changes)),
                "hidden_output_changes": self.hidden_changes,
                "output_symlinks": self.symlinks}
