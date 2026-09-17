import tempfile
import unittest
from pathlib import Path
from afm_hle.device_queue import DeviceQueue
from afm_hle.cli import payload

class DeviceQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'queue.sqlite3'
        self.q=DeviceQueue(self.path);self.addCleanup(self.q.close)
        self.body=payload({'question':'Reply exactly TEST_OK','image':''},'afm-cloud')
        self.q.enqueue('one','ipad',self.body)

    def result(self):return {'status':'done','model':'afm-cloud','text':'TEST_OK','error_code':None}

    def test_restart_does_not_replay_uncertain_dispatch(self):
        self.assertIsNotNone(self.q.claim('ipad'))
        other=DeviceQueue(self.path)
        try:self.assertIsNone(other.claim('ipad'))
        finally:other.close()

    def test_duplicate_submission_is_idempotent_and_conflict_rejected(self):
        job=self.q.claim('ipad')
        for _ in range(2):self.q.finish('ipad','one',job['payload_sha256'],self.result())
        changed=self.result();changed['text']='different'
        with self.assertRaises(ValueError):self.q.finish('ipad','one',job['payload_sha256'],changed)
        self.assertEqual(self.q.db.execute("SELECT count(*) FROM events WHERE action='done'").fetchone()[0],1)

    def test_identity_and_model_checked(self):
        self.assertIsNone(self.q.claim('other'))
        job=self.q.claim('ipad')
        with self.assertRaises(ValueError):self.q.finish('other','one',job['payload_sha256'],self.result())
        changed=self.result();changed['model']='afm-cloud-pro'
        with self.assertRaises(ValueError):self.q.finish('ipad','one',job['payload_sha256'],changed)

    def test_enqueue_cannot_replace_input_or_leak_dataset_columns(self):
        self.q.enqueue('one','ipad',self.body)
        with self.assertRaises(ValueError):self.q.enqueue('one','other',self.body)
        with self.assertRaises(ValueError):self.q.enqueue('two','ipad',dict(self.body,answer='reference'))

    def test_timeout_is_retained_without_requeue(self):
        job=self.q.claim('ipad')
        self.q.finish('ipad','one',job['payload_sha256'],{'status':'unknown','model':'afm-cloud','text':None,'error_code':'shortcut_timeout'})
        self.q.enqueue('two','ipad',self.body)
        self.assertIsNone(self.q.claim('ipad'))
        self.assertEqual(self.q.db.execute("SELECT state FROM jobs WHERE id='one'").fetchone()[0],'unknown')
