import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from afm_hle import cli,ipad_campaign as w

class IPadCampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.d=Path(self.tmp.name)
        self.data={'dataset':'synthetic','revision':'test','questions':[
            {'id':str(i),'question':'Question '+str(i),'answer':'SECRET REFERENCE','image':'data:image/png;base64,eA==' if i==4 else ''} for i in range(1,6)]}
        self.path=self.d/'data.json';cli.private_json(self.path,self.data)
        with cli.state(self.d/'generation.sqlite3') as db:
            cli.initialize(db,self.data,'http://127.0.0.1:1979')
            with db:
                db.execute("UPDATE items SET status='unknown' WHERE qid='1' AND model=?",(w.MODEL,))
                db.execute("INSERT INTO attempts(qid,model,status) VALUES('1',?,'unknown')",(w.MODEL,))
        plan={'data_path':str(self.path),'dataset_sha256':cli.digest(self.data),'sample_size':5,
              'selected_ids':['1','2','3','4','5'],'models':[w.MODEL]}
        cli.private_json(self.d/'plan.json',plan);(self.d/'plan.sha256').write_text(cli.digest(plan))
    def envelope(self,ticket,answer=b'Answer: A'):
        return io.BytesIO(ticket.encode()+b'\n'+base64.b64encode(answer))
    def start(self):
        w.enqueue(self.d,7);w.control(self.d,True);return w.claim(self.d)
    def test_order_ownership_image_boundary_and_no_reference_leak(self):
        r=w.enqueue(self.d,7);self.assertEqual(r['queued'],2);self.assertEqual(r['boundary']['ordinal'],4)
        self.assertEqual(w.enqueue(self.d,7)['queued'],0)
        with self.assertRaises(ValueError):w.claim(self.d)
        w.control(self.d,True);job=w.claim(self.d)
        self.assertEqual(job['ordinal'],2);self.assertNotIn('SECRET',json.dumps(job))
        with self.assertRaises(ValueError):w.claim(self.d)
        with cli.state(self.d/'generation.sqlite3') as db:
            cli.initialize(db,self.data,'http://127.0.0.1:1979')
            self.assertEqual(db.execute("SELECT status FROM items WHERE qid='2' AND model=?",(w.MODEL,)).fetchone()[0],'device_reserved')
    def test_pause_accepts_inflight_result_idempotently_and_separate_grades(self):
        job=self.start();w.control(self.d,False)
        self.assertFalse(w.submit(self.d,self.envelope(job['ticket']))['duplicate'])
        self.assertTrue(w.submit(self.d,self.envelope(job['ticket']))['duplicate'])
        with self.assertRaises(ValueError):w.submit(self.d,self.envelope(job['ticket'],b'different'))
        with self.assertRaises(ValueError):w.claim(self.d)
        w.sync_judging(self.d);w.sync_judging(self.d)
        with cli.state(self.d/'ipad-judging.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0],1)
            self.assertEqual(json.loads(db.execute('SELECT manifest FROM config').fetchone()[0])['device_id'],w.DEVICE)
        with w.queue(self.d) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0],1) # original unknown only
            self.assertEqual(db.execute("SELECT status FROM items WHERE qid='2' AND model=?",(w.MODEL,)).fetchone()[0],'device_done')
    def test_retry_creates_new_ticket_rejects_late_upload_and_retains_history(self):
        old=self.start()
        with self.assertRaises(ValueError):w.resolve(self.d,old['ticket'],'retry')
        w.resolve(self.d,old['ticket'],'retry',True)
        with self.assertRaises(ValueError):w.claim(self.d)
        w.control(self.d,True);new=w.claim(self.d)
        self.assertEqual(new['ordinal'],old['ordinal']);self.assertNotEqual(new['ticket'],old['ticket'])
        with self.assertRaises(ValueError):w.submit(self.d,self.envelope(old['ticket']))
        s=w.status(self.d);self.assertEqual(s['jobs'][0]['state'],'superseded');self.assertEqual(s['jobs'][1]['attempt'],2)
    def test_unknown_can_upload_existing_receipt_without_retry(self):
        job=self.start();w.resolve(self.d,job['ticket'],'unknown')
        self.assertEqual(w.submit(self.d,self.envelope(job['ticket']))['status'],'recorded')
    def test_cancel_only_queued_and_reenqueue_is_new_attempt(self):
        tickets=w.enqueue(self.d,1)['tickets'];w.resolve(self.d,tickets[0],'cancel')
        new=w.enqueue(self.d,1)['tickets'][0];self.assertNotEqual(new,tickets[0])
        w.control(self.d,True);w.claim(self.d)
        with self.assertRaises(ValueError):w.resolve(self.d,new,'cancel')
    def test_prompt_matches_hollis_contract(self):
        body=cli.payload(self.data['questions'][0],w.MODEL)
        self.assertEqual(w.render(body),'You are continuing an existing conversation.\n\nSYSTEM:\n'+cli.PROMPT+'\n\nUSER:\nQuestion 1\n\nRespond to the final USER message while preserving the conversation context.\n')
    def test_oversize_or_malformed_upload_never_finishes_job(self):
        job=self.start()
        for raw in [b'x'*(w.MAX_RESULT+1),job['ticket'].encode()+b'\n!notbase64!']:
            with self.assertRaises(ValueError):w.submit(self.d,io.BytesIO(raw))
        self.assertEqual(w.status(self.d)['saved'],0)

class ClaimHandoffTests(unittest.TestCase):
    setUp=IPadCampaignTests.setUp
    start=IPadCampaignTests.start
    envelope=IPadCampaignTests.envelope

    def test_next_claim_waits_for_existing_upload_without_replaying(self):
        from unittest.mock import patch
        old=self.start()
        def finish(_):w.submit(self.d,self.envelope(old['ticket']))
        with patch.object(w.time,'sleep',side_effect=finish):
            new=w.claim_wait(self.d,timeout=1)
        self.assertEqual(new['ordinal'],3)
        self.assertNotEqual(new['ticket'],old['ticket'])
        self.assertEqual(w.status(self.d)['saved'],1)

    def test_wait_timeout_leaves_same_attempt_and_quota_hold_never_waits(self):
        from unittest.mock import patch
        old=self.start()
        with self.assertRaises(ValueError):w.claim_wait(self.d,timeout=0)
        self.assertEqual(w.status(self.d)['jobs'][0]['state'],'inflight')
        w.resolve(self.d,old['ticket'],'rate_limited');w.control(self.d,True)
        with patch.object(w.time,'sleep') as sleep:
            with self.assertRaises(ValueError):w.claim_wait(self.d)
            sleep.assert_not_called()

if __name__=='__main__':unittest.main()

class GatewayMirrorTests(unittest.TestCase):
    setUp=IPadCampaignTests.setUp
    start=IPadCampaignTests.start
    envelope=IPadCampaignTests.envelope
    def test_metadata_mirror_excludes_question_answer_and_preserves_mac_tables(self):
        import sqlite3
        path=self.d/'gateway.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE holds(model TEXT,reason TEXT)')
            db.execute("INSERT INTO holds VALUES('afm-cloud-pro','quota')")
        job=self.start();w.submit(self.d,self.envelope(job['ticket'],b'PRIVATE_ANSWER'))
        self.assertEqual(w.mirror_gateway(self.d,path),2)
        self.assertEqual(w.mirror_gateway(self.d,path),2)
        with sqlite3.connect(path) as db:
            text=str(db.execute('SELECT * FROM worker_calls').fetchall())
            self.assertNotIn('PRIVATE_ANSWER',text);self.assertNotIn('Question',text)
            self.assertEqual(db.execute('SELECT count(*) FROM holds').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM worker_calls').fetchone()[0],2)
