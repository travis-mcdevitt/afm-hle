"""Checkpointed reference HLE grading through a local LiteLLM route."""
import argparse
import json
import math
import os
from pathlib import Path
import time
from .cli import digest, encode, request_bearer, stamp, state, MODELS
from .config import read_env

PROMPT = Path(__file__).with_name('judge_prompt.txt').read_text()
REFERENCE_REVISION = '73ae974b1844c3ffa64c3f4343d9f1f259575700'
SCHEMA = {'type': 'object', 'properties': {
    'extracted_final_answer': {'type': 'string'},
    'reasoning': {'type': 'string'},
    'correct': {'type': 'string', 'enum': ['yes', 'no']},
    'confidence': {'type': 'integer'},
    'strict': {'type': 'boolean', 'enum': [True]},
}, 'required': ['extracted_final_answer', 'reasoning', 'correct', 'confidence', 'strict'],
    'additionalProperties': False}


def schema_for(profile='reference'):
    schema = json.loads(json.dumps(SCHEMA))
    if profile == 'portable-boolean':
        schema['properties']['strict'].pop('enum')
    elif profile != 'reference':
        raise ValueError('unknown schema profile')
    return schema


def payload(question, response, model, schema_profile='reference'):
    return {'model': model, 'max_completion_tokens': 4096,
        'messages': [{'role': 'user', 'content': PROMPT.format(
            question=question['question'], correct_answer=question['answer'], response=response)}],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'ExtractedAnswer', 'strict': True, 'schema': schema_for(schema_profile)}}}


def validate(result):
    choice = result['choices'][0]
    if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
        raise ValueError('judge did not finish grading')
    grade = json.loads(choice['message']['content'])
    if set(grade) != set(SCHEMA['required']):
        raise ValueError('invalid grade fields')
    if grade['correct'] not in ('yes', 'no') or grade['strict'] is not True:
        raise ValueError('invalid verdict')
    if type(grade['confidence']) is not int or not 0 <= grade['confidence'] <= 100:
        raise ValueError('invalid confidence')
    if not all(isinstance(grade[k], str) for k in ('reasoning', 'extracted_final_answer')):
        raise ValueError('invalid extracted answer')
    return grade


def initialize(db, config, manifest, budget, input_rate, output_rate, schema_profile='reference'):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS judge_config (id INTEGER PRIMARY KEY, manifest TEXT);
      CREATE TABLE IF NOT EXISTS grades (attempt_id INTEGER PRIMARY KEY, status TEXT,
        started TEXT, finished TEXT, http INTEGER, latency_ms INTEGER, grade TEXT,
        raw_response TEXT, usage TEXT, estimated_cost_usd REAL, reported_cost_usd REAL,
        returned_model TEXT);
      CREATE TABLE IF NOT EXISTS grade_history (id INTEGER PRIMARY KEY, attempt_id INTEGER,
        archived_at TEXT, record TEXT);
    ''')
    spec = {'model': config['OPENAI_MODEL'], 'endpoint': config['OPENAI_BASE_URL'],
            'generation_manifest_sha256': digest(manifest), 'prompt_sha256': digest(PROMPT),
            'schema_sha256': digest(schema_for(schema_profile)), 'reference_revision': REFERENCE_REVISION,
            'max_completion_tokens': 4096, 'budget_usd': budget,
            'input_usd_per_million': input_rate, 'output_usd_per_million': output_rate}
    if schema_profile != 'reference': spec['schema_profile'] = schema_profile
    serialized = encode(spec).decode()
    old = db.execute('SELECT manifest FROM judge_config').fetchone()
    if old and old[0] != serialized:
        raise ValueError('judge configuration changed; use a separate evaluation database')
    with db:
        db.execute('INSERT OR IGNORE INTO judge_config VALUES(1,?)', (serialized,))
        db.execute("UPDATE grades SET status='unknown' WHERE status='inflight'")


def cost(result, headers, input_rate, output_rate):
    usage = result.get('usage', {})
    a, b = usage.get('prompt_tokens'), usage.get('completion_tokens')
    estimate = None
    if all(type(x) is int and x >= 0 for x in (a, b)):
        estimate = (a * input_rate + b * output_rate) / 1_000_000
    reported = next((v for k, v in headers.items() if k.lower() == 'x-litellm-response-cost'), None)
    if reported is not None:
        try:
            reported = float(reported)
            if not math.isfinite(reported) or reported < 0: reported = None
        except (ValueError, TypeError): reported = None
    return usage, estimate, reported


def run(args, transport=request_bearer):
    config = read_env(args.env_file)
    if getattr(args, 'model', None): config['OPENAI_MODEL'] = args.model
    if getattr(args, 'snapshot_from', None): snapshot(args.snapshot_from, args.db)
    data = json.loads(args.data.read_text())
    questions = {q['id']: q for q in data['questions']}
    with state(args.db) as db:
        row = db.execute('SELECT manifest FROM config').fetchone()
        if not row: raise ValueError('generation run missing')
        manifest = json.loads(row[0])
        if digest(data) != manifest['dataset_sha256']:
            raise ValueError('dataset does not match generation manifest')
        initialize(db, config, manifest, args.budget_usd, args.input_rate, args.output_rate, getattr(args, 'schema_profile', 'reference'))
        if getattr(args, 'retry_attempt', None) is not None:
            retry(db, args.retry_attempt)
        if db.execute("SELECT 1 FROM grades WHERE status != 'done'").fetchone():
            print('Judge paused on an unresolved attempt; no automatic replay.'); return 2
        attempts = db.execute("""SELECT a.* FROM attempts a JOIN items i ON a.qid=i.qid AND a.model=i.model
          LEFT JOIN grades g ON g.attempt_id=a.id
          WHERE a.status='done' AND i.status='done' AND g.attempt_id IS NULL ORDER BY a.id""").fetchall()
        for attempt in attempts[:args.max_calls]:
            body = payload(questions[attempt['qid']], attempt['response'], config['OPENAI_MODEL'], getattr(args, 'schema_profile', 'reference'))
            # Budgeted operation reserves a deliberately conservative UTF-8 byte bound
            # plus chat/schema framing, and all 4096 output tokens. No cap was requested
            # for the initial run, but the guard is available for future evaluations.
            if args.budget_usd is not None:
                spent = db.execute('SELECT coalesce(sum(estimated_cost_usd),0) FROM grades').fetchone()[0]
                unknown = db.execute('SELECT 1 FROM grades WHERE estimated_cost_usd IS NULL').fetchone()
                for history in db.execute('SELECT record FROM grade_history'):
                    previous = json.loads(history[0])
                    if previous['estimated_cost_usd'] is None: unknown = True
                    else: spent += previous['estimated_cost_usd']
                reserve = ((len(encode(body)) + 8192)*args.input_rate + 4096*args.output_rate)/1_000_000
                if unknown or spent + reserve > args.budget_usd:
                    print('Judge budget guard reached; no request sent.'); return 2
            with db:
                db.execute("INSERT INTO grades(attempt_id,status,started) VALUES(?,'inflight',?)", (attempt['id'], stamp()))
            start = time.monotonic()
            http, raw, grade, usage, estimate, reported, returned = None, None, None, None, None, None, None
            status = 'unknown'
            try:
                http, headers, result = transport(config['OPENAI_BASE_URL'], config['OPENAI_API_KEY'],
                    '/chat/completions', body, timeout=300)
                raw = json.dumps(result)
                if http == 200:
                    returned = result.get('model')
                    usage, estimate, reported = cost(result, headers, args.input_rate, args.output_rate)
                    grade = validate(result)
                    status = 'done'
                elif http == 429: status = 'paused'
                elif http >= 500: status = 'unknown'
                else: status = 'failed'
            except Exception:
                pass  # Raw diagnostics may contain benchmark content or credentials.
            with db:
                db.execute('''UPDATE grades SET status=?,finished=?,http=?,latency_ms=?,grade=?,raw_response=?,
                  usage=?,estimated_cost_usd=?,reported_cost_usd=?,returned_model=? WHERE attempt_id=?''',
                  (status, stamp(), http, round((time.monotonic()-start)*1000),
                   json.dumps(grade) if grade else None, raw,
                   json.dumps(usage) if usage is not None else None, estimate, reported, returned, attempt['id']))
            print(json.dumps({'judge_status': status, 'generation_attempt': attempt['id'],
                              'estimated_cost_usd': estimate}), flush=True)
            if status != 'done': return 2
        return 0


def snapshot(source, destination):
    """Copy generation records only into a new, generation-disabled judge DB."""
    source, destination = Path(source), Path(destination)
    if not source.is_file() or destination.exists():
        raise ValueError('snapshot requires an existing source and a new destination')
    try:
        with state(source) as original, state(destination) as target:
            if original.execute("SELECT 1 FROM items WHERE status='inflight'").fetchone():
                raise ValueError('source contains an interrupted generation')
            with target:
                for table in ('config', 'items', 'attempts', 'events'):
                    rows = original.execute('SELECT * FROM ' + table).fetchall()
                    if rows:
                        marks = ','.join('?' for _ in rows[0])
                        target.executemany('INSERT INTO ' + table + ' VALUES(' + marks + ')',
                                           [tuple(row) for row in rows])
                target.execute('CREATE TABLE generation_snapshot (created TEXT, responses_sha256 TEXT)')
                responses = [tuple(r) for r in original.execute(
                    'SELECT id,qid,model,response FROM attempts ORDER BY id')]
                target.execute('INSERT INTO generation_snapshot VALUES(?,?)', (stamp(), digest(responses)))
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def retry(db, attempt_id):
    row = db.execute('SELECT * FROM grades WHERE attempt_id=?', (attempt_id,)).fetchone()
    if not row or row['status'] == 'done':
        raise ValueError('only unresolved judge attempts may be retried')
    with db:
        db.execute('INSERT INTO grade_history(attempt_id,archived_at,record) VALUES(?,?,?)',
                   (attempt_id, stamp(), json.dumps(dict(row))))
        db.execute('DELETE FROM grades WHERE attempt_id=?', (attempt_id,))
    print('Judge retry explicitly authorized; previous attempt retained and may already have incurred cost.')


def wilson(correct, n):
    if not n: return None
    z = 1.95996398454
    p = correct/n
    center = (p + z*z/(2*n))/(1+z*z/n)
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [max(0,center-half), min(1,center+half)]


def report(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='judge_config'").fetchone():
        return None
    row = db.execute('SELECT manifest FROM judge_config').fetchone()
    if not row: return None
    spec = json.loads(row[0]); spec.pop('endpoint')
    result = {'configuration': spec, 'models': {}}
    for model in MODELS:
        rows = db.execute('''SELECT g.grade,g.usage,g.estimated_cost_usd,g.reported_cost_usd FROM grades g
          JOIN attempts a ON a.id=g.attempt_id WHERE g.status='done' AND a.model=?''', (model,)).fetchall()
        grades = [json.loads(r['grade']) for r in rows]
        n = len(grades); correct = sum(g['correct']=='yes' for g in grades)
        result['models'][model] = {'judged': n, 'correct': correct,
            'accuracy_on_judged': correct/n if n else None, 'wilson_95_interval': wilson(correct,n),
            'estimated_cost_usd': sum(r['estimated_cost_usd'] or 0 for r in rows),
            'calls_missing_cost_estimate': sum(r['estimated_cost_usd'] is None for r in rows)}
    pairs = {m: {} for m in MODELS}
    usages = []
    for row in db.execute("SELECT a.qid,a.model,g.grade,g.usage FROM grades g JOIN attempts a ON a.id=g.attempt_id WHERE g.status='done'"):
        pairs[row['model']][row['qid']] = json.loads(row['grade'])['correct'] == 'yes'
        if row['usage']: usages.append(json.loads(row['usage']))
    common = set(pairs[MODELS[0]]) & set(pairs[MODELS[1]])
    result['matched_pairs'] = {'count': len(common),
        'both_correct': sum(pairs[MODELS[0]][q] and pairs[MODELS[1]][q] for q in common),
        'cloud_only_correct': sum(pairs[MODELS[0]][q] and not pairs[MODELS[1]][q] for q in common),
        'pro_only_correct': sum(not pairs[MODELS[0]][q] and pairs[MODELS[1]][q] for q in common)}
    result['usage_completed_grades'] = {key: sum(u[key] for u in usages if type(u.get(key)) is int)
        for key in ('prompt_tokens','completion_tokens','total_tokens')}
    result['returned_models'] = {r[0]:r[1] for r in db.execute(
        "SELECT returned_model,count(*) FROM grades WHERE returned_model IS NOT NULL GROUP BY returned_model")}
    result['status_counts'] = {r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM grades GROUP BY status')}
    result['estimated_cost_usd_all_attempts'] = db.execute('SELECT sum(estimated_cost_usd) FROM grades').fetchone()[0]
    result['reported_cost_usd_all_attempts'] = db.execute('SELECT sum(reported_cost_usd) FROM grades').fetchone()[0]
    result['calls_missing_usage'] = db.execute('SELECT count(*) FROM grades WHERE usage IS NULL').fetchone()[0]
    history = [json.loads(r[0]) for r in db.execute('SELECT record FROM grade_history')]
    result['archived_retry_attempts'] = len(history)
    result['calls_missing_usage'] += sum(r['usage'] is None for r in history)
    result['cost_totals_exclude_unknown_attempts'] = result['calls_missing_usage'] > 0
    for field in ('estimated_cost_usd', 'reported_cost_usd'):
        known = [r[field] for r in history if r[field] is not None]
        if known:
            result[field + '_all_attempts'] = (result[field + '_all_attempts'] or 0) + sum(known)
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema-profile', choices=['reference','portable-boolean'], default='reference')
    parser.add_argument('--model', help='Override the judge model without changing .env')
    parser.add_argument('--snapshot-from', type=Path, help='Initialize a new DB from saved generations only; use once')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--db', type=Path, default=Path('.private/run.sqlite3'))
    parser.add_argument('--data', type=Path, default=Path('.private/hle.json'))
    parser.add_argument('--max-calls', type=int, default=10)
    parser.add_argument('--retry-attempt', type=int, help='Explicitly retry one unresolved generation-attempt ID')
    cap = parser.add_mutually_exclusive_group(required=True)
    cap.add_argument('--no-budget-cap', action='store_true')
    cap.add_argument('--budget-usd', type=float)
    parser.add_argument('--input-rate', type=float, required=True, help='USD per million input tokens')
    parser.add_argument('--output-rate', type=float, required=True, help='USD per million output tokens')
    args = parser.parse_args()
    if args.max_calls < 1 or any(not math.isfinite(x) or x < 0 for x in
        [args.input_rate,args.output_rate]+([] if args.budget_usd is None else [args.budget_usd])):
        parser.error('invalid limits or pricing')
    try: raise SystemExit(run(args))
    except Exception as e:
        print('Judge setup failed: ' + type(e).__name__ + '. No credentials or raw diagnostics displayed.')
        raise SystemExit(1)


if __name__ == '__main__': main()
