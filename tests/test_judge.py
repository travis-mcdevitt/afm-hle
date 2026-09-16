import argparse
import json
from pathlib import Path
import tempfile
import unittest
from afm_hle import cli, judge
from afm_hle.config import read_env


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name)
        self.data = {'dataset':'synthetic','revision':'v1','questions':[
            {'id':'a','question':'2+2?','answer':'4','image':''}]}
        cli.private_json(p/'data.json',self.data)
        (p/'.env').write_text('OPENAI_BASE_URL=http://localhost/v1\nOPENAI_MODEL=judge\nOPENAI_API_KEY="secret # literal"\n')
        self.args = argparse.Namespace(db=p/'run.sqlite3',data=p/'data.json',env_file=p/'.env',
            budget_usd=None,input_rate=1.1,output_rate=4.4,max_calls=10)
        with cli.state(self.args.db) as db:
            cli.initialize(db,self.data,'http://localhost:1979')
            with db:
                db.execute("UPDATE items SET status='done' WHERE model='afm-cloud'")
                db.execute("INSERT INTO attempts(qid,model,status,response) VALUES('a','afm-cloud','done','Answer: 4')")
        self.calls=[]

    def good(self,*args,**kwargs):
        self.calls.append(args)
        grade={'extracted_final_answer':'4','reasoning':'match','correct':'yes','confidence':90,'strict':True}
        return 200, {'x-litellm-response-cost':'0.00099'}, {'model':'judge',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(grade)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':200}}

    def test_resume_and_cost(self):
        self.assertEqual(judge.run(self.args,self.good),0)
        self.assertEqual(judge.run(self.args,self.good),0)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('secret',json.dumps(cli.report(self.args)))
        with cli.state(self.args.db) as db:
            report=judge.report(db)
        self.assertEqual(report['models']['afm-cloud']['correct'],1)
        self.assertAlmostEqual(report['estimated_cost_usd_all_attempts'],0.00099)

    def test_budget_prevents_request(self):
        self.args.budget_usd=0.0001
        self.assertEqual(judge.run(self.args,self.good),2)
        self.assertEqual(self.calls,[])

    def test_unknown_not_retried(self):
        def fail(*args,**kwargs): raise TimeoutError()
        self.assertEqual(judge.run(self.args,fail),2)
        self.assertEqual(judge.run(self.args,self.good),2)
        self.assertEqual(self.calls,[])

    def test_continue_flags_timeout_and_skips_it_on_resume(self):
        with cli.state(self.args.db) as db:
            with db:
                db.execute("UPDATE items SET status='done' WHERE model='afm-cloud-pro'")
                db.execute("INSERT INTO attempts(qid,model,status,response) VALUES('a','afm-cloud-pro','done','Answer: 4')")
        self.args.continue_on_error=True
        attempted=[]
        def flaky(*args,**kwargs):
            attempted.append(1)
            if len(attempted)==1:raise TimeoutError()
            return self.good(*args,**kwargs)
        self.assertEqual(judge.run(self.args,flaky),0)
        self.assertEqual(len(attempted),2)
        self.assertEqual(judge.run(self.args,flaky),0)
        self.assertEqual(len(attempted),2)
        with cli.state(self.args.db) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM grades ORDER BY attempt_id')],['unknown','done'])

    def test_continue_still_stops_on_judge_rate_limit(self):
        self.args.continue_on_error=True
        self.assertEqual(judge.run(self.args,lambda *a,**k:(429,{},{})),2)

    def test_invalid_grade_retains_usage(self):
        def bad(*args,**kwargs):
            status,headers,result=self.good(*args,**kwargs)
            result['choices'][0]['message']['content']='{}'
            return status,headers,result
        self.assertEqual(judge.run(self.args,bad),2)
        with cli.state(self.args.db) as db:
            r=db.execute('SELECT estimated_cost_usd,raw_response FROM grades').fetchone()
            self.assertGreater(r[0],0); self.assertIsNotNone(r[1])

    def test_explicit_retry_preserves_cost_history(self):
        def bad(*args,**kwargs):
            status,headers,result=self.good(*args,**kwargs)
            result['choices'][0]['message']['content']='{}'
            return status,headers,result
        judge.run(self.args,bad)
        self.args.retry_attempt=1
        judge.run(self.args,self.good)
        with cli.state(self.args.db) as db:
            report=judge.report(db)
            self.assertEqual(report['archived_retry_attempts'],1)
            self.assertAlmostEqual(report['estimated_cost_usd_all_attempts'],0.00198)
            with self.assertRaises(ValueError): judge.retry(db,1)

    def test_server_error_is_unknown_and_not_replayed(self):
        def overload(*args,**kwargs): return 502,{}, {'error': {'message':'overloaded'}}
        self.assertEqual(judge.run(self.args,overload),2)
        self.assertEqual(judge.run(self.args,self.good),2)
        with cli.state(self.args.db) as db:
            self.assertEqual(db.execute('SELECT status FROM grades').fetchone()[0],'unknown')
        self.assertEqual(self.calls,[])

    def test_alternative_snapshot_keeps_original_grades(self):
        from afm_hle.compare import compare
        judge.run(self.args,self.good)
        original=self.args.db
        self.args.db=original.with_name('alternative.sqlite3')
        judge.snapshot(original,self.args.db)
        self.args.model='other-judge'
        judge.run(self.args,self.good)
        result=compare(original,self.args.db)
        self.assertEqual(result['matched_grades'],1)
        self.assertEqual(result['models']['afm-cloud']['both_correct'],1)
        with cli.state(original) as db:
            self.assertEqual(json.loads(db.execute('SELECT manifest FROM judge_config').fetchone()[0])['model'],'judge')
        with self.assertRaises(ValueError):judge.snapshot(original,self.args.db)
        with self.assertRaises(ValueError):cli.run(self.args)
        with cli.state(self.args.db) as db:
            changed={'extracted_final_answer':'4','reasoning':'different verdict','correct':'no','confidence':90,'strict':True}
            with db:db.execute('UPDATE grades SET grade=?',(json.dumps(changed),))
        disagreement=compare(original,self.args.db)
        self.assertEqual(disagreement['models']['afm-cloud']['reference_only_correct'],1)
        with cli.state(self.args.db) as db:
            with db:db.execute("UPDATE attempts SET response='changed'")
        with self.assertRaises(ValueError):compare(original,self.args.db)

    def test_portable_boolean_retains_strict_local_validation(self):
        schema=judge.schema_for('portable-boolean')
        self.assertEqual(schema['properties']['strict'],{'type':'boolean'})
        self.assertEqual(judge.SCHEMA['properties']['strict']['enum'],[True])
        _,_,result=self.good()
        grade=json.loads(result['choices'][0]['message']['content'])
        grade['strict']=False
        result['choices'][0]['message']['content']=json.dumps(grade)
        with self.assertRaises(ValueError):judge.validate(result)

    def test_schema_comparison_requires_explicit_opt_in(self):
        from afm_hle.compare import compare
        judge.run(self.args,self.good)
        original=self.args.db
        self.args.db=original.with_name('portable.sqlite3')
        judge.snapshot(original,self.args.db)
        self.args.schema_profile='portable-boolean'
        judge.run(self.args,self.good)
        with self.assertRaises(ValueError):compare(original,self.args.db)
        self.assertIsNotNone(compare(original,self.args.db,True)['schema_variation'])
        self.args.schema_profile='reference'
        with self.assertRaises(ValueError):judge.run(self.args,self.good)

    def test_manifest_change_rejected(self):
        judge.run(self.args,self.good)
        self.args.input_rate=2
        with self.assertRaises(ValueError): judge.run(self.args,self.good)

    def test_dotenv_is_not_executed(self):
        self.assertEqual(read_env(self.args.env_file)['OPENAI_API_KEY'],'secret # literal')
        p=self.args.env_file
        p.write_text(p.read_text().replace('secret # literal','$(touch /tmp/do-not-execute)'))
        self.assertIn('$(touch',read_env(p)['OPENAI_API_KEY'])

    def test_wilson_edge_cases(self):
        self.assertIsNone(judge.wilson(0,0))
        lo,hi=judge.wilson(0,4)
        self.assertAlmostEqual(lo,0); self.assertGreater(hi,0)
        lo,hi=judge.wilson(4,4)
        self.assertLess(lo,1); self.assertAlmostEqual(hi,1)

if __name__ == '__main__': unittest.main()
