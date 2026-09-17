import io
import tempfile
import unittest
from pathlib import Path
from afm_hle.device_queue import DeviceQueue
from afm_hle.ipad_worker import prepare,dispatch,serve,MAX_MESSAGE,check_next,check_result

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

    def test_plain_shortcut_roundtrip_preserves_answer_and_blocks_rerun(self):
        self.assertEqual(check_next(self.queue,'ipad'),'Reply exactly "TEST_OK"')
        with self.assertRaises(ValueError):check_next(self.queue,'ipad')
        self.assertTrue(check_result(self.queue,'ipad',io.BytesIO(b'TEST_OK'))['exact_match'])
        self.assertTrue(check_result(self.queue,'ipad',io.BytesIO(b'TEST_OK'))['exact_match'])
        with self.assertRaises(ValueError):check_result(self.queue,'ipad',io.BytesIO(b'changed'))
        with self.assertRaises(ValueError):check_next(self.queue,'ipad')

    def test_ping_does_not_claim_or_execute(self):
        self.assertEqual(dispatch(self.queue,'ipad',{'operation':'ping'}),{'status':'ready','device':'ipad','model_called':False})
        self.assertEqual(self.queue.db.execute('SELECT state FROM jobs').fetchone()[0],'pending')

    def test_untrusted_operation_and_input_size_rejected(self):
        with self.assertRaises(ValueError):dispatch(self.queue,'ipad',{'operation':'next','device':'other'})
        with self.assertRaises(ValueError):serve(self.queue,'ipad',io.BytesIO(b'x'*(MAX_MESSAGE+1)))

    def test_qualification_rejects_real_questions_before_dispatch(self):
        self.queue.enqueue('real','other',{'model':'afm-cloud','stream':False,'messages':[{'role':'user','content':'HLE question'}]})
        with self.assertRaises(ValueError):dispatch(self.queue,'other',{'operation':'next'})
        self.assertEqual(self.queue.db.execute("SELECT state FROM jobs WHERE id='real'").fetchone()[0],'pending')

    def test_pro_check_uses_only_matching_route_and_retains_cloud_result(self):
        check_next(self.queue,'ipad')
        check_result(self.queue,'ipad',io.BytesIO(b'TEST_OK'))
        prepare(self.queue,'ipad','afm-cloud-pro')
        with self.assertRaises(ValueError):check_next(self.queue,'ipad')
        self.assertEqual(check_next(self.queue,'ipad','afm-cloud-pro'),'Reply exactly "TEST_OK"')
        with self.assertRaises(ValueError):check_next(self.queue,'ipad','afm-cloud-pro')
        result=check_result(self.queue,'ipad',io.BytesIO(b'TEST_OK'),'afm-cloud-pro')
        self.assertTrue(result['exact_match'])
        self.assertTrue(result['job_id'].endswith(':afm-cloud-pro'))
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM jobs WHERE state='done'").fetchone()[0],2)

    @unittest.skipUnless(Path('/usr/bin/textutil').exists(),'macOS RTF decoder')
    def test_rtf_receipt_does_not_replace_original_answer(self):
        check_next(self.queue,'ipad')
        raw=b'{\\rtf1\\ansi TEST_OK}'
        result=check_result(self.queue,'ipad',io.BytesIO(raw))
        self.assertTrue(result['exact_match'])
        self.assertFalse(result['raw_exact_match'])
        self.assertEqual(result['transport_encoding'],'rtf')
        import json
        saved=json.loads(self.queue.db.execute('SELECT result FROM jobs').fetchone()[0])
        self.assertEqual(saved['text'],raw.decode())
