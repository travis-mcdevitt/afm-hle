import argparse
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from afm_hle import campaign,cli,judge


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.root=root;private=root/'.private';private.mkdir()
        self.patch=patch.object(campaign,'ROOT',root);self.patch.start();self.addCleanup(self.patch.stop)
        questions=[{'id':str(n),'question':'Synthetic?','answer':'yes','image':'','category':'test'} for n in range(3)]
        questions.sort(key=lambda q:campaign.hashlib.sha256(('afm-hle-v1:'+q['id']).encode()).hexdigest())
        self.data={'dataset':'synthetic','revision':'test','questions':questions}
        cli.private_json(private/'hle.json',self.data)
        with cli.state(private/'run.sqlite3') as db:cli.initialize(db,self.data,'http://127.0.0.1:1979')
        judge.snapshot(private/'run.sqlite3',private/'gemini-flash.sqlite3')
        with cli.state(private/'gemini-flash.sqlite3') as db:
            manifest=json.loads(db.execute('SELECT manifest FROM config').fetchone()[0])
            judge.initialize(db,{'OPENAI_MODEL':'nous-gemini-3.7-flash','OPENAI_BASE_URL':'http://local'},manifest,None,.75,3.75,'portable-boolean')
        self.directory=private/'campaign';campaign.prepare(self.directory,1)
        self.calls=[]

    def generation(self,args):
        self.calls.append('generation')
        with cli.state(args.db) as db:
            with db:
                for q in self.data['questions'][:args.max_samples]:
                    for model in getattr(args,'models',cli.MODELS):
                        db.execute("UPDATE items SET status='done' WHERE qid=? AND model=?",(q['id'],model))
                        db.execute("INSERT INTO attempts(qid,model,status,response) VALUES(?,?,'done','Answer: yes')",(q['id'],model))
        return 0

    def grading(self,args):
        self.calls.append('grading')
        grade={'correct':'yes','confidence':100}
        with cli.state(args.db) as db:
            with db:
                for row in db.execute("SELECT id FROM attempts WHERE status='done'").fetchall():
                    db.execute("INSERT INTO grades(attempt_id,status,grade,estimated_cost_usd) VALUES(?,'done',?,?)",(row[0],json.dumps(grade),.01))
        return 0

    def test_fixed_sample_completes_and_preserves_pilot(self):
        campaign.work(self.directory,threading.Event(),self.generation,self.grading)
        result=campaign.stats(self.directory)
        self.assertEqual(result['phase'],'complete')
        self.assertEqual(self.calls,['generation','grading'])
        self.assertTrue(all(m['judged']==1 for m in result['models'].values()))
        with campaign.connect(self.root/'.private/run.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertNotIn('Answer: yes',json.dumps(result))

    def test_pro_only_scope(self):
        directory=self.root/'.private/pro-only'
        campaign.prepare(directory,1,['afm-cloud-pro'])
        campaign.work(directory,threading.Event(),self.generation,self.grading)
        result=campaign.stats(directory)
        self.assertEqual(result['phase'],'complete')
        self.assertEqual(list(result['models']),['afm-cloud-pro'])
        with campaign.connect(directory/'generation.sqlite3') as db:
            self.assertEqual([r[0] for r in db.execute('SELECT DISTINCT model FROM attempts')],['afm-cloud-pro'])

    def test_failure_stops_without_retries(self):
        def failure(args):self.calls.append('failure');return 2
        campaign.work(self.directory,threading.Event(),failure,self.grading)
        self.assertEqual(self.calls,['failure'])
        self.assertEqual(campaign.stats(self.directory)['phase'],'paused')

    def test_judge_timeout_does_not_pause_generation(self):
        def timeout(args):
            self.calls.append('judge-timeout')
            raise TimeoutError()
        campaign.work(self.directory,threading.Event(),self.generation,timeout)
        result=campaign.stats(self.directory)
        self.assertEqual(result['phase'],'generation_complete_grading_pending')
        self.assertTrue(result['grading_deferred'])
        self.assertEqual(self.calls,['generation','judge-timeout'])
        self.assertEqual(result['grading_pending'],2)

    def test_existing_grade_failure_allows_remaining_generation(self):
        q=self.data['questions'][0]
        with cli.state(self.directory/'generation.sqlite3') as db:
            with db:
                db.execute("UPDATE items SET status='done' WHERE qid=? AND model='afm-cloud-pro'",(q['id'],))
                ident=db.execute("INSERT INTO attempts(qid,model,status,response) VALUES(?,'afm-cloud-pro','done','saved')",(q['id'],)).lastrowid
        campaign.sync(self.directory/'generation.sqlite3',self.directory/'judging.sqlite3')
        with cli.state(self.directory/'judging.sqlite3') as db:
            with db:db.execute("INSERT INTO grades(attempt_id,status,raw_response) VALUES(?,'unknown','preserved failure')",(ident,))
        def remaining(args):
            self.calls.append('generation')
            with cli.state(args.db) as db:
                with db:
                    db.execute("UPDATE items SET status='done' WHERE qid=? AND model='afm-cloud'",(q['id'],))
                    db.execute("INSERT INTO attempts(qid,model,status,response) VALUES(?,'afm-cloud','done','saved')",(q['id'],))
            return 0
        campaign.work(self.directory,threading.Event(),remaining,self.grading)
        result=campaign.stats(self.directory)
        self.assertEqual(self.calls,['generation'])
        self.assertEqual(result['phase'],'generation_complete_grading_pending')
        self.assertEqual(result['grading_failures_for_retry'],1)
        with campaign.connect(self.directory/'judging.sqlite3') as db:
            self.assertEqual(db.execute('SELECT raw_response FROM grades').fetchone()[0],'preserved failure')

    def test_plan_tampering_rejected(self):
        path=self.directory/'plan.json';data=json.loads(path.read_text());data['sample_size']=2;cli.private_json(path,data)
        with self.assertRaises(ValueError):campaign.stats(self.directory)

    def test_snapshot_cannot_overwrite_changed_answers(self):
        self.generation(argparse.Namespace(db=self.directory/'generation.sqlite3',max_samples=1))
        campaign.sync(self.directory/'generation.sqlite3',self.directory/'judging.sqlite3')
        with cli.state(self.directory/'generation.sqlite3') as db:
            with db:db.execute("UPDATE attempts SET response='different'")
        with self.assertRaises(ValueError):campaign.sync(self.directory/'generation.sqlite3',self.directory/'judging.sqlite3')

    def test_no_progress_pauses(self):
        campaign.work(self.directory,threading.Event(),lambda a:0,self.grading)
        self.assertEqual(campaign.stats(self.directory)['phase'],'paused')

if __name__=='__main__':unittest.main()
