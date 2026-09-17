import io
import tempfile
import unittest
from pathlib import Path
from afm_hle.device_queue import DeviceQueue
from afm_hle.ipad_worker import prepare,dispatch,serve,MAX_MESSAGE

class IPadWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.queue=DeviceQueue(Path(self.tmp.name)/'queue.sqlite3');self.addCleanup(self.queue.close)
        prepare(self.queue,'ipad','afm-cloud')

    def test_full_roundtrip_and_duplicate_upload(self):
        job=dispatch(self.queue,'ipad',{'operation':'next'})
        self.assertEqual(job['prompt'],'Reply exactly "TEST_OK"')
        reply={'operation':'result','job_id':job['job_id'],'payload_sha256':job['payload_sha256'],
            'model':'afm-cloud','status':'done','text':'TEST_OK','error_code':None}
        self.assertTrue(dispatch(self.queue,'ipad',reply)['exact_match'])
        self.assertTrue(dispatch(self.queue,'ipad',reply)['exact_match'])
        self.assertEqual(dispatch(self.queue,'ipad',{'operation':'next'})['status'],'no_work_or_held')

    def test_untrusted_operation_and_input_size_rejected(self):
        with self.assertRaises(ValueError):dispatch(self.queue,'ipad',{'operation':'next','device':'other'})
        with self.assertRaises(ValueError):serve(self.queue,'ipad',io.BytesIO(b'x'*(MAX_MESSAGE+1)))

    def test_qualification_rejects_real_questions_before_dispatch(self):
        self.queue.enqueue('real','other',{'model':'afm-cloud','stream':False,'messages':[{'role':'user','content':'HLE question'}]})
        with self.assertRaises(ValueError):dispatch(self.queue,'other',{'operation':'next'})
        self.assertEqual(self.queue.db.execute("SELECT state FROM jobs WHERE id='real'").fetchone()[0],'pending')
