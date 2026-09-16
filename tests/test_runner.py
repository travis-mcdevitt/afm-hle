import argparse
import json
from pathlib import Path
import tempfile
import unittest
from afm_hle import cli


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.data = {'dataset': 'synthetic', 'revision': 'v1', 'questions': [
            {'id': 'a', 'question': '2+2?', 'answer': '4', 'image': ''},
            {'id': 'b', 'question': '3+3?', 'answer': '6', 'image': ''}]}
        self.args = argparse.Namespace(db=root/'run.sqlite3', data=root/'data.json',
            endpoint='http://localhost:1979', token_file=root/'token', max_calls=100)
        cli.private_json(self.args.data, self.data)
        self.calls = []

    def ok(self, endpoint, token, path, body=None):
        if path == '/monitor': return 200, {}, {'holds': []}
        self.calls.append(body)
        return 200, {'X-AFM-Request-ID': str(len(self.calls))}, {
            'model': body['model'], 'choices': [{'message': {'content': 'Answer: 4'}, 'finish_reason': 'stop'}]}

    def test_resume_never_repeats_completed(self):
        self.args.max_calls = 1
        cli.run(self.args, self.ok)
        self.args.max_calls = 100
        cli.run(self.args, self.ok)
        cli.run(self.args, self.ok)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual([b['model'] for b in self.calls], list(cli.MODELS)*2)

    def test_quota_stops_both_routes_and_requires_resolution(self):
        def quota(*args):
            if args[2] == '/monitor': return 200, {}, {'holds': []}
            self.calls.append(args)
            return 429, {'X-AFM-Request-ID': 'quota'}, {'error': {'code': 'rate_limited'}}
        self.assertEqual(cli.run(self.args, quota), 2)
        self.assertEqual(cli.run(self.args, self.ok), 2)
        self.assertEqual(len(self.calls), 1)

    def test_transport_not_retried(self):
        def fail(*args):
            if args[2] == '/monitor': return 200, {}, {'holds': []}
            raise TimeoutError()
        self.assertEqual(cli.run(self.args, fail), 2)
        self.assertEqual(cli.run(self.args, self.ok), 2)
        self.assertEqual(self.calls, [])

    def test_crash_recovery_is_unknown(self):
        with cli.state(self.args.db) as db:
            cli.initialize(db, self.data, self.args.endpoint)
            with db: db.execute("UPDATE items SET status='inflight' WHERE qid='a'")
        self.assertEqual(cli.run(self.args, self.ok), 2)
        self.assertEqual(self.calls, [])

    def test_changed_dataset_rejected(self):
        cli.run(self.args, self.ok)
        self.data['questions'][0]['question'] = 'modified'
        cli.private_json(self.args.data, self.data)
        with self.assertRaises(ValueError): cli.run(self.args, self.ok)
        self.assertEqual(len(self.calls), 4)

    def test_answers_not_sent_or_reported(self):
        self.data['questions'][0]['answer'] = 'SECRET_REFERENCE'
        cli.private_json(self.args.data, self.data)
        cli.run(self.args, self.ok)
        self.assertNotIn('SECRET_REFERENCE', json.dumps(self.calls))
        result = cli.report(self.args)
        self.assertNotIn('Answer: 4', json.dumps(result))
        self.assertIsNone(result['apple_token_usage'])
        self.assertIsNone(result['accuracy'])

    def test_holds_prevent_generation(self):
        def held(*args): return 200, {}, {'holds': [{'model': 'afm-cloud'}]}
        self.assertEqual(cli.run(self.args, held), 2)
        self.assertEqual(self.calls, [])

    def test_explicit_retry_retains_attempt_history(self):
        def quota(*args):
            if args[2] == '/monitor': return 200, {}, {'holds': []}
            return 429, {'X-AFM-Request-ID': 'q'}, {'error': {'code': 'rate_limited'}}
        cli.run(self.args, quota)
        cli.resolve(argparse.Namespace(db=self.args.db, id='a', model='afm-cloud', action='retry'))
        cli.run(self.args, self.ok)
        with cli.state(self.args.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)

    def test_images_and_parameter_contract(self):
        q = dict(self.data['questions'][0], image='data:image/png;base64,YQ==')
        body = cli.payload(q, 'afm-cloud')
        self.assertEqual(set(body), {'model', 'messages', 'stream'})
        self.assertEqual(body['messages'][-1]['content'][1]['type'], 'image_url')
        q['image'] = 'https://example.com/image.png'
        with self.assertRaises(ValueError): cli.payload(q, 'afm-cloud')

    def test_bad_response_is_unknown(self):
        for response in [{}, {'model': 'wrong', 'choices': [{'message': {'content': 'x'}}]}]:
            self.assertEqual(cli.classify(200, response, 'afm-cloud')[0], 'unknown')

    def test_lock_blocks_concurrent_run(self):
        with cli.state(self.args.db):
            with self.assertRaises(ValueError):
                with cli.state(self.args.db): pass


if __name__ == '__main__': unittest.main()
