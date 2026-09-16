"""Detached, bounded HLE sampling campaign with a read-only local dashboard."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import cli, judge

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / '.private/campaign'
MODELS = cli.MODELS


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def prepare(directory, size=400, models=MODELS, port=1981):
    directory = Path(directory).resolve()
    if (directory/'plan.json').exists(): raise ValueError('campaign already exists')
    data_path = ROOT/'.private/hle.json'
    data = json.loads(data_path.read_text())
    if not 1 <= size <= len(data['questions']): raise ValueError('invalid sample size')
    questions = data['questions']
    expected = sorted(questions, key=lambda q: hashlib.sha256(('afm-hle-v1:'+q['id']).encode()).hexdigest())
    if [q['id'] for q in questions] != [q['id'] for q in expected]:
        raise ValueError('dataset order is not the frozen hash permutation')
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Independent continuation databases preserve all pilot experiments.
    for src, name in [(ROOT/'.private/run.sqlite3','generation.sqlite3'),
                      (ROOT/'.private/gemini-flash.sqlite3','judging.sqlite3')]:
        with cli.state(src) as original:
            if original.execute("SELECT 1 FROM attempts WHERE status='inflight'").fetchone():
                raise ValueError('pilot generation is still in flight')
            with sqlite3.connect(directory/name) as target: original.backup(target)
        (directory/name).chmod(0o600)
    plan = {'version':1,'models':list(models),'created_at':cli.stamp(),'sample_size':size,
        'population_size':len(questions),'dataset_sha256':cli.digest(data),
        'dataset_revision':data['revision'],'ordering':'sha256(afm-hle-v1:id)',
        'selected_ids':[q['id'] for q in questions[:size]],
        'selected_ids_sha256':cli.digest([q['id'] for q in questions[:size]]),
        'image_questions':sum(bool(q.get('image')) for q in questions[:size]),
        'category_counts':{},'data_path':str(data_path),
        'judge_model':'nous-gemini-3.7-flash','schema_profile':'portable-boolean',
        'input_rate':0.75,'output_rate':3.75,'budget_usd':None,'batch_calls':10,
        'port':port,'automatic_retries':False,'automatic_quota_resume':False}
    for q in questions[:size]:
        key=q.get('category','unknown');plan['category_counts'][key]=plan['category_counts'].get(key,0)+1
    cli.private_json(directory/'plan.json',plan)
    (directory/'plan.sha256').write_text(cli.digest(plan))
    cli.private_json(directory/'state.json',{'phase':'ready','updated_at':cli.stamp()})
    return plan


def load_plan(directory):
    directory=Path(directory)
    plan=json.loads((directory/'plan.json').read_text())
    if cli.digest(plan)!=(directory/'plan.sha256').read_text().strip():
        raise ValueError('campaign plan changed')
    return plan


def sync(source, target):
    """Append immutable generations to the judge DB; never overwrite grades."""
    with cli.state(source) as a, cli.state(target) as b:
        if a.execute('SELECT manifest FROM config').fetchone()[0] != b.execute('SELECT manifest FROM config').fetchone()[0]:
            raise ValueError('generation provenance mismatch')
        originals={r['id']:dict(r) for r in a.execute('SELECT * FROM attempts')}
        for row in b.execute('SELECT * FROM attempts'):
            if originals.get(row['id']) != dict(row): raise ValueError('saved generation changed')
        with b:
            for row in originals.values():
                marks=','.join('?' for _ in row)
                b.execute('INSERT OR IGNORE INTO attempts VALUES('+marks+')',tuple(row.values()))
            for row in a.execute('SELECT qid,model,status FROM items'):
                b.execute('UPDATE items SET status=? WHERE qid=? AND model=?',(row['status'],row['qid'],row['model']))
            responses=[tuple(r) for r in a.execute('SELECT id,qid,model,response FROM attempts ORDER BY id')]
            b.execute('UPDATE generation_snapshot SET responses_sha256=?',(cli.digest(responses),))


def stats(directory):
    directory=Path(directory)
    plan=load_plan(directory)
    state=json.loads((directory/'state.json').read_text())
    selected=set(plan['selected_ids'])
    result={'phase':state['phase'],'phase_updated_at':state['updated_at'],
        'detail':state.get('detail'),'generated_at':cli.stamp(),
        'target_per_model':plan['sample_size'],'population_size':plan['population_size'],
        'sample_ids_sha256':plan['selected_ids_sha256'],'judge_model':plan['judge_model'],
        'models_requested':plan['models'],'sample_image_questions':plan['image_questions'],'models':{},
        'apple_token_usage':None,'apple_remaining_quota':None,'apple_reset_at':None,
        'automatic_retries':False,'automatic_quota_resume':False,
        'confidence_interval_note':'Pointwise Wilson intervals; provisional while incomplete. No accuracy-based early stopping.'}
    with contextlib.closing(connect(directory/'generation.sqlite3')) as db:
        items=[dict(r) for r in db.execute('SELECT * FROM items') if r['qid'] in selected and r['model'] in plan['models']]
        attempts=[dict(r) for r in db.execute('SELECT * FROM attempts') if r['qid'] in selected and r['model'] in plan['models']]
    with contextlib.closing(connect(directory/'judging.sqlite3')) as db:
        grades=[dict(r) for r in db.execute('SELECT g.*,a.qid,a.model FROM grades g JOIN attempts a ON a.id=g.attempt_id') if r['qid'] in selected and r['model'] in plan['models']]
        history=[json.loads(r['record']) for r in db.execute('SELECT h.record,a.qid,a.model FROM grade_history h JOIN attempts a ON a.id=h.attempt_id') if r['qid'] in selected and r['model'] in plan['models']]
    all_grades=grades+history
    known=[r['estimated_cost_usd'] for r in all_grades if r['estimated_cost_usd'] is not None]
    result['judge_known_cost_usd']=sum(known)
    result['judge_unknown_cost_attempts']=sum(r['estimated_cost_usd'] is None and r['status']!='inflight' for r in all_grades)
    result['judge_inflight']=sum(r['status']=='inflight' for r in grades)
    result['judge_errors']=sum(r['status'] not in ('done','inflight') for r in grades)
    result['grading_deferred']=(directory/'grading-deferred.json').exists() or bool(result['judge_errors'] or result['judge_inflight'])
    result['grading_failures_for_retry']=result['judge_errors']
    result['grading_pending']=sum(r['status']=='done' for r in items)-sum(r['status']=='done' for r in grades)
    result['judge_usage']={k:0 for k in ['prompt_tokens','completion_tokens','reasoning_tokens']}
    for g in all_grades:
        u=json.loads(g['usage']) if g['usage'] else {}
        for key in ('prompt_tokens','completion_tokens'):
            if type(u.get(key)) is int:result['judge_usage'][key]+=u[key]
        reasoning=u.get('completion_tokens_details',{}).get('reasoning_tokens')
        if type(reasoning) is int:result['judge_usage']['reasoning_tokens']+=reasoning
    for model in plan['models']:
        rows=[r for r in items if r['model']==model]
        done=[g for g in grades if g['model']==model and g['status']=='done']
        correct=sum(json.loads(g['grade'])['correct']=='yes' for g in done)
        counts={}
        for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
        result['models'][model]={'generation':counts,'judged':len(done),'correct':correct,
            'accuracy_on_judged':correct/len(done) if done else None,
            'wilson_95_interval':judge.wilson(correct,len(done))}
    if attempts:
        last=max(attempts,key=lambda r:r['id'])
        result['last_generation']={k:last[k] for k in ['model','status','started','finished','latency_ms','code']}
    return result


def publish(directory):
    cli.private_json(Path(directory)/'progress.json',stats(directory))


def set_phase(directory, phase, detail=None):
    cli.private_json(Path(directory)/'state.json',{'phase':phase,'updated_at':cli.stamp(),'detail':detail})
    publish(directory)


def work(directory, stop, generation=cli.run, grading=judge.run):
    directory=Path(directory)
    plan=load_plan(directory)
    data=Path(plan['data_path'])
    if cli.digest(json.loads(data.read_text())) != plan['dataset_sha256']:
        raise ValueError('dataset changed')
    gen=argparse.Namespace(db=directory/'generation.sqlite3',data=data,
        endpoint='http://127.0.0.1:1979',token_file=cli.TOKEN,
        max_calls=plan['batch_calls'],max_samples=plan['sample_size'],models=plan['models'])
    grade=argparse.Namespace(db=directory/'judging.sqlite3',data=data,env_file=ROOT/'.env',
        model=plan['judge_model'],schema_profile=plan['schema_profile'],
        budget_usd=plan['budget_usd'],input_rate=plan['input_rate'],output_rate=plan['output_rate'],
        max_calls=plan['batch_calls'],max_samples=plan['sample_size'],models=plan['models'],retry_attempt=None)
    while not stop.is_set():
        s=stats(directory)
        if any(any(v for k,v in m['generation'].items() if k in ('unknown','paused','blocked','inflight','skipped')) for m in s['models'].values()):
            set_phase(directory,'paused','Generation requires explicit resolution; no retry performed.');return
        generated=sum(m['generation'].get('done',0) for m in s['models'].values())
        graded=sum(m['judged'] for m in s['models'].values())
        target=plan['sample_size']*len(plan['models'])
        if generated==target and graded==target:
            set_phase(directory,'complete','Fixed sample completed.');return
        if generated==target and s['grading_deferred']:
            set_phase(directory,'generation_complete_grading_pending','All answers saved. Grading failures and backlog await explicit retry.');return
        # A judge failure defers grading for this campaign; it cannot block generation.
        if graded < generated and not s['grading_deferred']:
            sync(gen.db,grade.db)
            set_phase(directory,'grading')
            try:
                failed=grading(grade)!=0
            except Exception:
                failed=True
            if failed:
                cli.private_json(directory/'grading-deferred.json',{
                    'created_at':cli.stamp(),'reason':'Judge failure; grading deferred for explicit future retry.'})
                set_phase(directory,'generating','Grading deferred after failure; continuing Apple generation.')
                continue
        else:
            set_phase(directory,'generating','Grading deferred; failures and backlog retained for future retry.' if s['grading_deferred'] else None)
            if generation(gen)!=0:
                sync(gen.db,grade.db)
                set_phase(directory,'paused','Gateway hold, generation failure, or unresolved outcome; no retry performed.');return
            sync(gen.db,grade.db)
        publish(directory)
        after=stats(directory)
        progress=sum(m['generation'].get('done',0)+m['judged'] for m in after['models'].values())
        if progress == generated+graded:
            set_phase(directory,'paused','Batch made no progress; no automatic retry.');return
    set_phase(directory,'stopped','Stopped between batches; rerun worker to resume.')


HTML='''<!doctype html><html><head><meta charset="utf-8"><title>AFM × HLE live</title><style>
body{background:#10151d;color:#eef2f6;font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 24px}h1{font-size:30px} .cards{display:flex;gap:20px;flex-wrap:wrap}.card{background:#1d2633;padding:24px;border-radius:12px;flex:1;min-width:260px}progress{width:100%;height:20px;accent-color:#6fe0c1}small,.muted{color:#abb8c9} .big{font-size:36px;font-weight:650}#phase{color:#6fe0c1}pre{white-space:pre-wrap;font:14px system-ui}a{color:#6fe0c1}</style></head><body>
<h1>AFM × Humanity’s Last Exam</h1><p>Fixed HLE sample · <span id="phase">Connecting…</span></p><p id="detail"></p>
<div id="cards" class="cards"></div><div class="card" style="margin-top:20px"><p id="cost"></p><p id="grading"></p><p id="usage"></p><p id="last"></p><small id="updated"></small></div>
<p class="muted">Scores are provisional until the full sample is graded. Error bars are pointwise Wilson 95% intervals, not sequential stopping rules. Apple token usage and quota reset times are unavailable.</p>
<p class="muted">Runs locally without an AI observer. Refreshes every 3 seconds. Apple quota and generation failures pause inference. Judge failures defer grading while generation continues; this page never retries requests.</p><a href="/api/status">Aggregate JSON</a>
<script>
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
async function refresh(){try{let r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error();let s=await r.json();document.getElementById('phase').textContent=s.phase;document.getElementById('detail').textContent=s.detail||'';
document.getElementById('cards').innerHTML=Object.entries(s.models).map(([name,m])=>{let n=s.target_per_model,done=m.generation.done||0,ci=m.wilson_95_interval;return `<div class="card"><h2>${name}</h2><div class="big">${done} / ${n}</div><p>Answers saved</p><progress value="${done}" max="${n}"></progress><p>${m.judged} graded · ${m.correct} correct</p><progress value="${m.judged}" max="${n}"></progress><h2>${pct(m.accuracy_on_judged)}</h2><small>95% interval ${ci?ci.map(pct).join(' – '):'—'} · denominator: graded answers</small></div>`}).join('');
document.getElementById('cost').textContent='Judge: '+s.judge_model+' · known cost $'+s.judge_known_cost_usd.toFixed(4)+' · unknown-cost attempts '+s.judge_unknown_cost_attempts;
document.getElementById('grading').textContent='Grading: '+(s.grading_deferred?'deferred — ':'active — ')+s.grading_failures_for_retry+' failures flagged for retry · '+s.grading_pending+' answers awaiting valid grades';
document.getElementById('usage').textContent='Judge tokens — input '+s.judge_usage.prompt_tokens.toLocaleString()+', completion '+s.judge_usage.completion_tokens.toLocaleString()+', reasoning '+s.judge_usage.reasoning_tokens.toLocaleString();
document.getElementById('last').textContent=s.last_generation?'Last generation: '+s.last_generation.model+' · '+s.last_generation.status:'';
document.getElementById('updated').textContent='Updated '+new Date(s.generated_at).toLocaleTimeString()+' · sample '+s.target_per_model+' of '+s.population_size;
}catch(e){document.getElementById('phase').textContent='Disconnected — check the local process/log';}}refresh();setInterval(refresh,3000);
</script></body></html>'''


def worker(directory):
    directory=Path(directory).resolve()
    lock=(directory/'worker.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    plan=load_plan(directory)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            if self.path not in ('/','/api/status'):
                self.send_error(404);return
            try:
                body=HTML.encode() if self.path=='/' else json.dumps(stats(directory)).encode()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if self.path=='/' else 'application/json')
                self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:self.send_error(503,'Status temporarily unavailable')
    server=ThreadingHTTPServer(('127.0.0.1',plan['port']),Handler)
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    def background():
        awake=subprocess.Popen(['/usr/bin/caffeinate','-i'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:work(directory,stop)
        except Exception as e:
            set_phase(directory,'paused','Worker error: '+type(e).__name__+'; inspect local setup. No automatic retry.')
        finally:
            awake.terminate();awake.wait()
    thread=threading.Thread(target=background,daemon=False)
    thread.start()
    server.timeout=1
    # Serve after completion/pause so final state remains visible, with no inference.
    while not stop.is_set():server.handle_request()
    server.server_close()


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','start','worker','status','stop'])
    p.add_argument('--directory',type=Path,default=DEFAULT)
    p.add_argument('--size',type=int,default=400)
    p.add_argument('--port',type=int,default=1981)
    p.add_argument('--models',nargs='+',choices=MODELS,default=['afm-cloud-pro'])
    args=p.parse_args();d=args.directory.resolve()
    if args.command=='prepare':
        plan=prepare(d,args.size,args.models,args.port)
        print(json.dumps({k:v for k,v in plan.items() if k not in ('selected_ids','data_path')}))
    elif args.command=='status':print(json.dumps(stats(d),indent=2))
    elif args.command=='worker':worker(d)
    elif args.command=='stop':
        pid=json.loads((d/'process.json').read_text())['pid']
        command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
        if 'afm_hle.campaign worker' not in command or str(d) not in command:
            raise ValueError('PID no longer belongs to this campaign')
        os.kill(pid,signal.SIGTERM)
        print('Stop requested; current batch will checkpoint before exit.')
    else:
        # Parent exits immediately; no Codex agent, polling loop, or scheduler involved.
        with (d/'worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with (d/'worker.log').open('ab',buffering=0) as log:
            proc=subprocess.Popen([sys.executable,'-u','-m','afm_hle.campaign','worker','--directory',str(d)],
                cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
        cli.private_json(d/'process.json',{'pid':proc.pid,'started_at':cli.stamp()})
        print(json.dumps({'detached_pid':proc.pid,'dashboard':'http://127.0.0.1:'+str(load_plan(d)['port']),'log':str(d/'worker.log')}))


if __name__=='__main__':main()
