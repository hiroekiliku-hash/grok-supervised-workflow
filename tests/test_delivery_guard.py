"""Independent filesystem evidence checks: no CLI and no model service."""
import os
import hashlib
from unittest.mock import patch
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'runtime'))
from artifact_scan import changed_paths, file_snapshot
from delivery_guard import DeliveryGuard
from bridge import source_drift


class DeliveryEvidence(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.root = self.parent / 'workspace'
        self.root.mkdir()

    def guard(self, mode='edit'):
        return DeliveryGuard(self.root, ['output.py'] if mode == 'edit' else [], mode, 0, 1, 1)

    def test_snapshot_ignores_hidden_symlink_and_nonregular_files(self):
        (self.root/'normal.txt').write_text('included')
        (self.root/'.hidden').write_text('excluded')
        (self.root/'.cache').mkdir(); (self.root/'.cache/secret').write_text('excluded')
        (self.parent/'outside.txt').write_text('must not read')
        (self.root/'link.txt').symlink_to(self.parent/'outside.txt')
        (self.root/'linked-dir').symlink_to(self.parent, target_is_directory=True)
        os.mkfifo(self.root/'pipe')
        self.assertEqual(set(file_snapshot(self.root)), {'normal.txt'})

    def test_hashes_contents_not_mtime(self):
        p=self.root/'output.py';p.write_text('before')
        stamp=p.stat();before=file_snapshot(self.root)
        p.write_text('after!');os.utime(p, ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        after=file_snapshot(self.root)
        self.assertEqual(changed_paths(before,after), ['output.py'])
        p.touch();self.assertEqual(changed_paths(after,file_snapshot(self.root)), [])

    def test_reverted_file_is_not_delivery(self):
        p=self.root/'output.py';p.write_text('baseline');g=self.guard()
        p.write_text('changed');g.observe(.2)
        self.assertEqual(g.snapshot(.2)['artifact_change_count'],1)
        p.write_text('baseline');g.observe(.4,force=True)
        self.assertEqual(g.completion_failure(),'missing_delivery')

    def test_late_artifact_cannot_rescue_expired_attempt(self):
        g=self.guard();(self.root/'output.py').write_text('late')
        g.observe(1.2,force=True)
        self.assertEqual(g.reason(1.2),'artifact_timeout')
        self.assertEqual(g.completion_failure(),'artifact_timeout')

    def test_review_mutation_is_rejected(self):
        g=self.guard('review');(self.root/'unexpected.txt').write_text('changed')
        g.observe(.2,force=True)
        self.assertEqual(g.completion_failure(),'unexpected_outputs')

    def test_output_symlink_is_rejected(self):
        (self.parent/'outside').write_text('private');g=self.guard()
        (self.root/'output.py').symlink_to(self.parent/'outside')
        g.observe(.2,force=True)
        self.assertEqual(g.completion_failure(),'output_boundary_violation')


    def manifest(self):
        info=self.root.stat()
        return {'source_root_identity':{'device':info.st_dev,'inode':info.st_ino},
                'files':[{'path':'output.py','sha256':hashlib.sha256(b'original').hexdigest()}]}

    def test_source_symlink_loop_is_drift_not_crash(self):
        (self.root/'output.py').write_text('original');manifest=self.manifest()
        (self.root/'output.py').unlink();(self.root/'output.py').symlink_to('output.py')
        self.assertEqual(source_drift(self.root,manifest),['output.py'])

    def test_redirected_source_root_is_not_read(self):
        (self.root/'output.py').write_text('original');manifest=self.manifest()
        self.root.rename(self.parent/'saved')
        other=self.parent/'other';other.mkdir();(other/'output.py').write_text('original')
        self.root.symlink_to(other,target_is_directory=True)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('Redirected root read')):
            self.assertEqual(source_drift(self.root,manifest),['.','output.py'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
