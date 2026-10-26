"""Hash workspace files without following symlinks or reading hidden paths."""
import errno
import hashlib
import os
from pathlib import Path
import stat


def file_snapshot(workspace):
    root = Path(workspace).resolve(strict=True)
    result = {}

    def walk_error(exc):
        if not isinstance(exc, FileNotFoundError):
            raise exc

    for parent, directories, files, directory_fd in os.fwalk(root, follow_symlinks=False, onerror=walk_error):
        directories[:] = [name for name in directories if not name.startswith('.')]
        for name in files:
            if name.startswith('.'):
                continue
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.ELOOP):
                    continue
                raise
            with os.fdopen(descriptor, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    continue
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(65536), b''):
                    digest.update(chunk)
            result[(Path(parent) / name).relative_to(root).as_posix()] = digest.hexdigest()
    return result


def changed_paths(before, after):
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
