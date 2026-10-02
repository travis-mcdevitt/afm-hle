import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from afm_hle import cli,ipad_campaign as w,ipad_image_check as check
from tests import test_ipad_campaign as helpers

class ImageQueueTests(unittest.TestCase):
    setUp=helpers.IPadCampaignTests.setUp
    envelope=helpers.IPadCampaignTests.envelope
    def test_image_qualification_and_matching_worker_preserve_bytes_and_text(self):
        from PIL import Image
        im=Image.new('RGB',(20,20),'red');raw=io.BytesIO();im.save(raw,format='PNG')
        encoded=base64.b64encode(raw.getvalue()).decode()
        self.data['questions'][3]['image']='data:image/png;base64,'+encoded
        cli.private_json(self.path,self.data)
        plan=json.loads((self.d/'plan.json').read_text());plan['dataset_sha256']=cli.digest(self.data)
        cli.private_json(self.d/'plan.json',plan);(self.d/'plan.sha256').write_text(cli.digest(plan))
        with w.queue(self.d) as db,db:
            manifest=json.loads(db.execute('SELECT manifest FROM config').fetchone()[0]);manifest['dataset_sha256']=plan['dataset_sha256']
            db.execute('UPDATE config SET manifest=?',(cli.encode(manifest).decode(),))
        w.enqueue(self.d,7);w.control(self.d,True)
        for _ in range(2):
            job=w.claim(self.d);w.submit(self.d,self.envelope(job['ticket']))
        self.assertEqual(w.enqueue(self.d,1)['queued'],0)
        cli.private_json(self.d/'ipad-image-qualification.json',{'qualified':True})
        self.assertEqual(w.enqueue(self.d,1)['queued'],1)
        with self.assertRaises(ValueError):w.claim(self.d)
        job=w.claim(self.d,image_mode=True)
        self.assertEqual(job['image_base64'],encoded)
        self.assertEqual(job['protocol'],w.IMAGE_PROTOCOL)
        self.assertNotIn(encoded,job['prompt'])
        w.submit(self.d,self.envelope(job['ticket']))
        w.sync_judging(self.d,w.IMAGE_PROTOCOL)
        with cli.state(self.d/'ipad-image-judging.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0],1)

class QualificationTests(unittest.TestCase):
    def test_pixel_code_not_in_prompt_and_no_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'check.json';check.prepare(p);record=json.loads(p.read_text())
            job=check.claim(p);self.assertNotIn(record['expected'],job['prompt'])
            with self.assertRaises(ValueError):check.claim(p)
            receipt=job['ticket'].encode()+b'\n'+base64.b64encode(record['expected'].encode())
            self.assertTrue(check.submit(io.BytesIO(receipt),p)['qualified'])
