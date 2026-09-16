"""Private checkpoints, explicit retries, and public aggregate reporting."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.request

from .images import gateway_image, POLICY

MODELS = ('afm-cloud', 'afm-cloud-pro')
# From centerforaisafety/hle (MIT); see docs/METHODOLOGY.md and NOTICE.
PROMPT = ('Your response should be in the following format:\n'
          'Explanation: {your explanation for your answer choice}\n'
          'Answer: {your chosen answer}\n'
          'Confidence: {your confidence score between 0% and 100% for your answer}')
TOKEN = Path.home() / 'Library/Application Support/afm-gateway/gateway.token'
LIMIT = 8 * 1024 * 1024


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(encode(value) + b'\n')
    tmp.chmod(0o600)
    tmp.replace(path)


def request(endpoint, token_file, path, body=None):
    return request_bearer(endpoint, Path(token_file).read_text().strip(), path, body)


def request_bearer(endpoint, token, path, body=None, timeout=160):
    # No retry, redirect, fallback, or SDK default retry behavior.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    req = urllib.request.Request(endpoint.rstrip('/') + path,
        data=encode(body) if body is not None else None,
        headers={'Authorization': 'Bearer ' + token,
                 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('oversized response')
        return response.status, dict(response.headers), json.loads(raw)


def payload(question, model):
    content = [{'type': 'text', 'text': question['question']}]
    image = question.get('image')
    if image:
        image = gateway_image(image)
        content.append({'type': 'image_url', 'image_url': {'url': image}})
    body = {'model': model, 'stream': False, 'messages': [
        {'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': content}]}
    if len(encode(body)) > LIMIT:
        raise ValueError('request exceeds 8 MiB')
    return body


def prepare(args):
    from huggingface_hub import HfApi, hf_hub_download
    import pyarrow.parquet as pq
    info = HfApi().dataset_info('cais/hle', revision=args.revision)
    path = hf_hub_download('cais/hle', 'data/test-00000-of-00001.parquet',
        repo_type='dataset', revision=info.sha, cache_dir=str(args.output.parent / 'hf-cache'))
    rows = pq.read_table(path, columns=['id', 'question', 'image', 'answer',
                                       'answer_type', 'raw_subject', 'category']).to_pylist()
    # Stable hash order avoids subject/order bias in interrupted prefixes.
    rows.sort(key=lambda q: hashlib.sha256(('afm-hle-v1:' + q['id']).encode()).hexdigest())
    private_json(args.output, {'dataset': 'cais/hle', 'revision': info.sha,
        'split': 'test', 'selection': 'sha256(afm-hle-v1:id)', 'questions': rows})
    print(json.dumps({'revision': info.sha, 'questions': len(rows),
                      'images': sum(bool(q.get('image')) for q in rows)}))


@contextlib.contextmanager
def state(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another process owns this run')
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('''
          CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, manifest TEXT);
          CREATE TABLE IF NOT EXISTS items (qid TEXT, model TEXT, status TEXT,
            PRIMARY KEY(qid,model));
          CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, qid TEXT, model TEXT,
            started TEXT, finished TEXT, status TEXT, http INTEGER, code TEXT,
            request_id TEXT, latency_ms INTEGER, response TEXT, finish_reason TEXT);
          CREATE TABLE IF NOT EXISTS events (time TEXT, qid TEXT, model TEXT, action TEXT);
        ''')
        path.chmod(0o600)
        try:
            yield db
        finally:
            db.close()


def initialize(db, data, endpoint):
    questions = data['questions']
    ids = [q['id'] for q in questions]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('dataset must contain unique, nonempty question IDs')
    if data['dataset'] == 'cais/hle' and not re.fullmatch(r'[0-9a-f]{40}', data['revision']):
        raise ValueError('HLE revision must be a commit SHA')
    manifest = {'dataset': data['dataset'], 'revision': data['revision'],
                'dataset_sha256': digest(data), 'models': MODELS,
                'prompt_sha256': digest(PROMPT), 'endpoint': endpoint,
                'protocol': 'afm-hle-v2', 'image_policy': POLICY, 'total_questions': len(questions)}
    serialized = encode(manifest).decode()
    existing = db.execute('SELECT manifest FROM config WHERE id=1').fetchone()
    if existing and existing[0] != serialized:
        raise ValueError('run manifest changed; use a new database')
    with db:
        db.execute('INSERT OR IGNORE INTO config VALUES(1,?)', (serialized,))
        for qid in ids:
            for model in MODELS:
                db.execute("INSERT OR IGNORE INTO items VALUES(?,?,'pending')", (qid, model))
        db.execute("UPDATE items SET status='unknown' WHERE status='inflight'")
        db.execute("UPDATE attempts SET status='unknown' WHERE status='inflight'")
    return manifest


def migrate_images(args):
    """Upgrade v1 only when all dispatched inputs are identical under v2."""
    data = json.loads(args.data.read_text())
    questions = {q['id']: q for q in data['questions']}
    with state(args.db) as db:
        old = json.loads(db.execute('SELECT manifest FROM config WHERE id=1').fetchone()[0])
        if old['protocol'] != 'afm-hle-v1' or old['dataset_sha256'] != digest(data):
            raise ValueError('migration requires an unchanged v1 dataset')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='judge_config'").fetchone():
            raise ValueError('cannot migrate a run already configured for judging')
        for attempt in db.execute('SELECT qid,status FROM attempts'):
            image = questions[attempt['qid']].get('image')
            if attempt['status'] == 'inflight' or (image and gateway_image(image) != image):
                raise ValueError('already dispatched inputs would change')
        new = dict(old, protocol='afm-hle-v2', image_policy=POLICY)
        with db:
            db.execute('UPDATE config SET manifest=? WHERE id=1', (encode(new).decode(),))
            db.execute('INSERT INTO events VALUES(?,?,?,?)',
                       (stamp(), None, None, 'migrate_v1_to_v2_images; original_manifest=' + encode(old).decode()))
        print('Image policy upgraded; all previously dispatched payloads remain unchanged.')


def classify(status, result, model):
    error = result.get('error') or {}
    code = error.get('code', 'http_error') if isinstance(error, dict) else 'http_error'
    if status == 429:
        return 'paused', code, None, None
    if status != 200:
        return ('unknown' if status >= 500 else 'blocked'), code, None, None
    try:
        choice = result['choices'][0]
        answer = choice['message']['content']
        if result['model'] != model or not isinstance(answer, str) or not answer.strip():
            raise ValueError()
        return 'done', None, answer, choice.get('finish_reason')
    except (KeyError, IndexError, TypeError, ValueError):
        return 'unknown', 'invalid_response', None, None


def run(args, transport=request):
    data = json.loads(args.data.read_text())
    with state(args.db) as db:
        initialize(db, data, args.endpoint)
        if db.execute("SELECT 1 FROM items WHERE status IN ('unknown','paused','blocked')").fetchone():
            print('Run paused: resolve outstanding items before continuing.'); return 2
        status, _, monitor = transport(args.endpoint, args.token_file, '/monitor')
        if status != 200 or monitor.get('holds'):
            print('Gateway unavailable or holds present; no generation attempted.'); return 2
        count = 0
        for q in data['questions']:
            for model in MODELS:
                current = db.execute('SELECT status FROM items WHERE qid=? AND model=?', (q['id'], model)).fetchone()[0]
                if current != 'pending':
                    continue
                if count >= args.max_calls:
                    return 0
                try:
                    body = payload(q, model)
                except ValueError:
                    with db:
                        db.execute("UPDATE items SET status='blocked' WHERE qid=? AND model=?", (q['id'], model))
                    print('Input unsupported; item blocked without a provider request.'); return 2
                with db:
                    db.execute("UPDATE items SET status='inflight' WHERE qid=? AND model=?", (q['id'], model))
                    ident = db.execute("INSERT INTO attempts(qid,model,started,status) VALUES(?,?,?,'inflight')",
                                       (q['id'], model, stamp())).lastrowid
                start = time.monotonic()
                http, rid = None, None
                try:
                    http, headers, result = transport(args.endpoint, args.token_file, '/v1/chat/completions', body)
                    rid = next((v for k, v in headers.items() if k.lower() == 'x-afm-request-id'), None)
                    outcome, code, answer, finish = classify(http, result, model)
                    if not rid:
                        outcome, code = 'unknown', 'missing_gateway_request_id'
                except Exception:
                    outcome, code, answer, finish = 'unknown', 'transport_or_protocol', None, None
                with db:
                    db.execute('UPDATE attempts SET finished=?,status=?,http=?,code=?,request_id=?,latency_ms=?,response=?,finish_reason=? WHERE id=?',
                               (stamp(), outcome, http, code, rid, round((time.monotonic()-start)*1000), answer, finish, ident))
                    db.execute('UPDATE items SET status=? WHERE qid=? AND model=?', (outcome, q['id'], model))
                count += 1
                print(json.dumps({'model': model, 'status': outcome, 'call': count, 'request_id': rid}), flush=True)
                if outcome != 'done':
                    return 2
        return 0


def resolve(args):
    with state(args.db) as db:
        row = db.execute('SELECT status FROM items WHERE qid=? AND model=?', (args.id, args.model)).fetchone()
        if not row or row[0] not in ('unknown', 'paused', 'blocked', 'inflight'):
            raise ValueError('only unresolved items can be resolved')
        with db:
            db.execute('UPDATE items SET status=? WHERE qid=? AND model=?',
                       ('pending' if args.action == 'retry' else 'skipped', args.id, args.model))
            db.execute('INSERT INTO events VALUES(?,?,?,?)', (stamp(), args.id, args.model, args.action))
        print('Resolution recorded; retry may consume quota again. Gateway holds are unchanged.')


def report(args):
    with state(args.db) as db:
        config = db.execute('SELECT manifest FROM config').fetchone()
        if not config:
            raise ValueError('run not initialized')
        manifest = json.loads(config[0])
        manifest.pop('endpoint')  # local infrastructure is not public provenance
        result = {'generated_at': stamp(), 'manifest': manifest, 'models': {},
                  'accuracy': None, 'judge_status': 'not_configured',
                  'apple_token_usage': None, 'apple_remaining_quota': None,
                  'apple_reset_at': None, 'model_build_identity': 'not_exposed_by_shortcuts'}
        for model in MODELS:
            counts = {r[0]: r[1] for r in db.execute('SELECT status,count(*) FROM items WHERE model=? GROUP BY status', (model,))}
            stats = db.execute('SELECT count(*),sum(latency_ms) FROM attempts WHERE model=?', (model,)).fetchone()
            result['models'][model] = {'items': counts, 'gateway_requests_started': stats[0],
                'total_latency_ms': stats[1], 'completion_fraction': counts.get('done', 0)/manifest['total_questions']}
        for model in MODELS:
            result['models'][model]['finish_reasons'] = {r[0]:r[1] for r in db.execute(
                "SELECT finish_reason,count(*) FROM attempts WHERE model=? AND status='done' GROUP BY finish_reason", (model,))}
            result['models'][model]['error_codes'] = {r[0]:r[1] for r in db.execute(
                "SELECT code,count(*) FROM attempts WHERE model=? AND code IS NOT NULL GROUP BY code", (model,))}
            responses = [r[0] for r in db.execute("SELECT response FROM attempts WHERE model=? AND status='done'", (model,))]
            result['models'][model]['responses_without_confidence_label'] = sum('confidence:' not in r.lower() for r in responses)
        from .judge import report as judge_report
        grading = judge_report(db)
        if grading is not None:
            result['grading'] = grading
            result['judge_status'] = 'partial'
            if all(grading['models'][m]['judged'] == manifest['total_questions'] for m in MODELS):
                result['judge_status'] = 'complete'
                result['accuracy'] = {m: grading['models'][m]['accuracy_on_judged'] for m in MODELS}
        return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--revision', required=True)
    p.add_argument('--output', type=Path, default=Path('.private/hle.json'))
    for cmd in ['run', 'report', 'resolve', 'migrate-images']:
        p = sub.add_parser(cmd)
        p.add_argument('--db', type=Path, default=Path('.private/run.sqlite3'))
        if cmd == 'migrate-images':
            p.add_argument('--data', type=Path, default=Path('.private/hle.json'))
        if cmd == 'run':
            p.add_argument('--data', type=Path, default=Path('.private/hle.json'))
            p.add_argument('--endpoint', default='http://127.0.0.1:1979')
            p.add_argument('--token-file', type=Path, default=TOKEN)
            p.add_argument('--max-calls', type=int, default=10)
        if cmd == 'resolve':
            p.add_argument('--id', required=True)
            p.add_argument('--model', choices=MODELS, required=True)
            p.add_argument('--action', choices=['retry', 'skip'], required=True)
    args = parser.parse_args()
    try:
        if args.command == 'run':
            if args.max_calls < 1: parser.error('--max-calls must be positive')
            raise SystemExit(run(args))
        elif args.command == 'prepare': prepare(args)
        elif args.command == 'resolve': resolve(args)
        elif args.command == 'migrate-images': migrate_images(args)
        else: print(json.dumps(report(args), indent=2))
    except Exception as e:
        # Do not print raw provider errors, URLs, dataset contents, or secrets.
        print('Operation failed: ' + type(e).__name__ + '. Check private configuration and dependency access.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
