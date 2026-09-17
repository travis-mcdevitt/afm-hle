"""Mac-controlled, ticketed iPad HLE queue. No automatic model retries.

Ownership lives in the original campaign DB. iPad answers and grades are a
separate device stratum; the original Mac generations remain immutable.
"""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import campaign, cli, judge

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / '.private/campaign'
DEVICE = 'ipad-air-m3'
MODEL = 'afm-cloud-pro'
PROTOCOL = 'ipad-ssh-ticketed-text-v1'
MAX_RESULT = 2 * 1024 * 1024


def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS ipad_jobs (
        ticket TEXT PRIMARY KEY, qid TEXT NOT NULL, model TEXT NOT NULL,
        ordinal INTEGER NOT NULL, attempt INTEGER NOT NULL, state TEXT NOT NULL,
        payload TEXT NOT NULL, payload_hash TEXT NOT NULL, prompt TEXT NOT NULL,
        prompt_hash TEXT NOT NULL, created TEXT NOT NULL, started TEXT,
        finished TEXT, raw_b64 TEXT, answer TEXT, encoding TEXT,
        UNIQUE(qid,model,attempt));
      CREATE TABLE IF NOT EXISTS ipad_controls (
        id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
        updated TEXT NOT NULL, heartbeat TEXT, detail TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS ipad_events (
        id INTEGER PRIMARY KEY, time TEXT NOT NULL, ticket TEXT, action TEXT NOT NULL);
      INSERT OR IGNORE INTO ipad_controls VALUES(1,0,'',NULL,'Not armed');
    ''')


@contextlib.contextmanager
def queue(directory):
    with cli.state(Path(directory)/'generation.sqlite3') as db:
        schema(db)
        yield db


def event(db, ticket, action):
    db.execute('INSERT INTO ipad_events(time,ticket,action) VALUES(?,?,?)',
               (cli.stamp(), ticket, action))


def render(body):
    """Exact Hollis chat.RenderTranscript for the HLE system/user text shape."""
    messages = body['messages']
    if len(messages)!=2 or messages[0]['role']!='system' or messages[1]['role']!='user':
        raise ValueError('unsupported prompt shape')
    content=messages[1]['content']
    if len(content)!=1 or content[0]['type']!='text':
        raise ValueError('image transport is not yet qualified; no question skipped')
    prompt=('You are continuing an existing conversation.\n\nSYSTEM:\n'+messages[0]['content']+
            '\n\nUSER:\n'+content[0]['text']+
            '\n\nRespond to the final USER message while preserving the conversation context.\n')
    if len(prompt.encode())>128*1024:raise ValueError('prompt too large')
    return prompt


def add_job(db, qid, ordinal, body, prompt):
    attempt=db.execute('SELECT coalesce(max(attempt),0)+1 FROM ipad_jobs WHERE qid=? AND model=?',
                       (qid,MODEL)).fetchone()[0]
    ticket=str(uuid.uuid4())
    db.execute('''INSERT INTO ipad_jobs(ticket,qid,model,ordinal,attempt,state,payload,payload_hash,
                  prompt,prompt_hash,created) VALUES(?,?,?,?,?,'queued',?,?,?,?,?)''',
               (ticket,qid,MODEL,ordinal,attempt,cli.encode(body).decode(),cli.digest(body),prompt,
                hashlib.sha256(prompt.encode()).hexdigest(),cli.stamp()))
    db.execute("UPDATE items SET status='device_reserved' WHERE qid=? AND model=?",(qid,MODEL))
    event(db,ticket,'reserved:'+DEVICE)
    return ticket


def enqueue(directory, count):
    if type(count) is not int or not 1<=count<=20:raise ValueError('batch must contain 1–20 questions')
    plan=campaign.load_plan(directory)
    data=json.loads(Path(plan['data_path']).read_text())
    if cli.digest(data)!=plan['dataset_sha256']:raise ValueError('dataset changed')
    if MODEL not in plan['models']:raise ValueError('not a Pro campaign')
    added=[]; boundary=None
    with queue(directory) as db:
        manifest=json.loads(db.execute('SELECT manifest FROM config WHERE id=1').fetchone()[0])
        if manifest['dataset_sha256']!=plan['dataset_sha256']:raise ValueError('manifest mismatch')
        with db:
            for ordinal,q in enumerate(data['questions'][:plan['sample_size']],1):
                if q['id']!=plan['selected_ids'][ordinal-1]:raise ValueError('sample order changed')
                item=db.execute('SELECT status FROM items WHERE qid=? AND model=?',(q['id'],MODEL)).fetchone()
                if not item or item[0]!='pending':continue
                # Do not move an earlier uncertain/retried Mac attempt to iPad.
                if db.execute('SELECT 1 FROM attempts WHERE qid=? AND model=?',(q['id'],MODEL)).fetchone():continue
                if q.get('image'):
                    boundary={'ordinal':ordinal,'reason':'image transport awaiting qualification'};break
                body=cli.payload(q,MODEL);prompt=render(body)
                added.append(add_job(db,q['id'],ordinal,body,prompt))
                if len(added)>=count:break
            event(db,None,'enqueue:'+str(len(added)))
    return {'queued':len(added),'tickets':added,'boundary':boundary}


def control(directory, enabled):
    with queue(directory) as db, db:
        db.execute('UPDATE ipad_controls SET enabled=?,updated=?,detail=? WHERE id=1',
                   (int(enabled),cli.stamp(),'Armed; waiting for iPad pull' if enabled else 'Paused from Mac; current call may still finish'))
        event(db,None,'resume' if enabled else 'pause')
    return {'enabled':enabled}


def claim(directory):
    with queue(directory) as db, db:
        db.execute('UPDATE ipad_controls SET heartbeat=? WHERE id=1',(cli.stamp(),))
        if not db.execute('SELECT enabled FROM ipad_controls').fetchone()[0]:raise ValueError('Queue paused on Mac')
        if db.execute("SELECT 1 FROM ipad_jobs WHERE state IN ('inflight','unknown','rate_limited','failed')").fetchone():
            raise ValueError('Previous iPad attempt unresolved; retry upload or resolve on Mac')
        row=db.execute("SELECT * FROM ipad_jobs WHERE state='queued' ORDER BY ordinal,attempt LIMIT 1").fetchone()
        if not row:raise ValueError('No queued questions; stopped without inference')
        db.execute("UPDATE ipad_jobs SET state='inflight',started=? WHERE ticket=?",(cli.stamp(),row['ticket']))
        event(db,row['ticket'],'claimed')
        return {'ticket':row['ticket'],'prompt':row['prompt'],'model':MODEL,
                'ordinal':row['ordinal'],'payload_sha256':row['payload_hash'],'protocol':PROTOCOL}


def decode_answer(raw):
    text=raw.decode('utf-8')
    if not text.strip():raise ValueError('empty answer')
    if text.startswith('{\\rtf'):
        text=subprocess.run(['/usr/bin/textutil','-convert','txt','-format','rtf','-stdin','-stdout'],
                            input=raw,capture_output=True,check=True,timeout=10).stdout.decode('utf-8')
        encoding='rtf-textutil-v1'
    else:encoding='utf8'
    if not text.strip():raise ValueError('empty decoded answer')
    return text,encoding


def submit(directory, source):
    """Envelope = immutable UUID, newline, base64 UTF-8 answer. No shell interpolation."""
    envelope=source.read(MAX_RESULT+1)
    if len(envelope)>MAX_RESULT:raise ValueError('result too large')
    ticket,encoded=envelope.decode('utf-8').split('\n',1)
    if str(uuid.UUID(ticket))!=ticket:raise ValueError('invalid ticket')
    raw=base64.b64decode(''.join(encoded.split()),validate=True)
    canonical=base64.b64encode(raw).decode()
    answer,encoding=decode_answer(raw)
    with queue(directory) as db, db:
        row=db.execute('SELECT * FROM ipad_jobs WHERE ticket=?',(ticket,)).fetchone()
        if not row:raise ValueError('unknown ticket')
        if row['state']=='done':
            if row['raw_b64']!=canonical:raise ValueError('conflicting upload')
            return {'status':'recorded','ordinal':row['ordinal'],'duplicate':True,'ticket':ticket}
        if row['state'] not in ('inflight','unknown','rate_limited','failed'):
            # Never attribute a late response to a replacement attempt.
            raise ValueError('attempt superseded or cancelled; preserve local receipt for review')
        if row['raw_b64'] is not None:raise ValueError('answer already recorded')
        db.execute("UPDATE ipad_jobs SET state='done',finished=?,raw_b64=?,answer=?,encoding=? WHERE ticket=?",
                   (cli.stamp(),canonical,answer,encoding,ticket))
        db.execute("UPDATE items SET status='device_done' WHERE qid=? AND model=?",(row['qid'],MODEL))
        event(db,ticket,'answer_saved:'+encoding)
        return {'status':'recorded','ordinal':row['ordinal'],'duplicate':False,'ticket':ticket}


def resolve(directory,ticket,action,acknowledge=False):
    if action not in ('retry','unknown','rate_limited','skip','cancel'):raise ValueError('invalid resolution')
    with queue(directory) as db, db:
        row=db.execute('SELECT * FROM ipad_jobs WHERE ticket=?',(ticket,)).fetchone()
        if not row:raise ValueError('unknown ticket')
        state=row['state']
        if action=='cancel':
            if state!='queued':raise ValueError('only unclaimed work can be cancelled')
            db.execute("UPDATE ipad_jobs SET state='cancelled',finished=? WHERE ticket=?",(cli.stamp(),ticket))
            db.execute("UPDATE items SET status='pending' WHERE qid=? AND model=?",(row['qid'],MODEL))
        else:
            if state not in ('inflight','unknown','rate_limited','failed'):raise ValueError('only unresolved calls can be resolved')
            if action in ('retry','skip') and not acknowledge:
                raise ValueError('Stop the iPad worker first; acknowledge the uncertain call and possible duplicate quota use')
            if action=='retry':
                db.execute("UPDATE ipad_jobs SET state='superseded',finished=? WHERE ticket=?",(cli.stamp(),ticket))
                new=add_job(db,row['qid'],row['ordinal'],json.loads(row['payload']),row['prompt'])
                event(db,new,'retry_of:'+ticket)
            elif action=='skip':
                db.execute("UPDATE ipad_jobs SET state='skipped',finished=? WHERE ticket=?",(cli.stamp(),ticket))
                db.execute("UPDATE items SET status='device_skipped' WHERE qid=? AND model=?",(row['qid'],MODEL))
            else:
                db.execute('UPDATE ipad_jobs SET state=? WHERE ticket=?',(action,ticket))
            # Resolution does not itself arm/resume inference.
            db.execute('UPDATE ipad_controls SET enabled=0,updated=?,detail=? WHERE id=1',
                       (cli.stamp(),'Resolution recorded; resume explicitly'))
        event(db,ticket,action)
    return {'status':'resolved','action':action}


def sync_judging(directory):
    """Append completed iPad answers to a separate, generation-disabled grade DB."""
    directory=Path(directory)
    with queue(directory) as source:
        manifest=json.loads(source.execute('SELECT manifest FROM config').fetchone()[0])
        rows=[dict(r) for r in source.execute("SELECT rowid AS seq,* FROM ipad_jobs WHERE state='done' ORDER BY rowid")]
    manifest.update(device_id=DEVICE,device_model='iPad Air 11-inch M3',os='iPadOS 27.0',
                    protocol=PROTOCOL,transport='restricted SSH pull',image_transport='not qualified')
    with cli.state(directory/'ipad-judging.sqlite3') as db:
        existing=db.execute('SELECT manifest FROM config').fetchone()
        serialized=cli.encode(manifest).decode()
        if existing and existing[0]!=serialized:raise ValueError('iPad manifest changed')
        with db:
            db.execute('INSERT OR IGNORE INTO config VALUES(1,?)',(serialized,))
            db.execute('CREATE TABLE IF NOT EXISTS generation_snapshot (created TEXT,responses_sha256 TEXT)')
            if not db.execute('SELECT 1 FROM generation_snapshot').fetchone():db.execute('INSERT INTO generation_snapshot VALUES(?,?)',(cli.stamp(),''))
            for r in rows:
                latency=round((dt.datetime.fromisoformat(r['finished'])-dt.datetime.fromisoformat(r['started'])).total_seconds()*1000)
                old=db.execute('SELECT response,request_id FROM attempts WHERE id=?',(r['seq'],)).fetchone()
                if old and tuple(old)!=(r['answer'],r['ticket']):raise ValueError('saved answer changed')
                db.execute("INSERT OR IGNORE INTO items VALUES(?,?,'done')",(r['qid'],MODEL))
                db.execute('''INSERT OR IGNORE INTO attempts(id,qid,model,started,finished,status,request_id,latency_ms,response,finish_reason)
                              VALUES(?,?,?,?,?,'done',?,?,?,'stop')''',
                           (r['seq'],r['qid'],MODEL,r['started'],r['finished'],r['ticket'],latency,r['answer']))
            responses=[tuple(r) for r in db.execute('SELECT id,qid,model,response FROM attempts ORDER BY id')]
            db.execute('UPDATE generation_snapshot SET responses_sha256=?',(cli.digest(responses),))


def grade(directory, retry_id=None):
    sync_judging(directory)
    plan=campaign.load_plan(directory)
    args=argparse.Namespace(db=Path(directory)/'ipad-judging.sqlite3',data=Path(plan['data_path']),
        env_file=ROOT/'.env',model=plan['judge_model'],schema_profile=plan['schema_profile'],
        budget_usd=plan['budget_usd'],input_rate=plan['input_rate'],output_rate=plan['output_rate'],
        max_calls=20,max_samples=plan['sample_size'],models=[MODEL],retry_attempt=retry_id,continue_on_error=True)
    return judge.run(args)


def status(directory):
    with queue(directory) as db:
        c=dict(db.execute('SELECT * FROM ipad_controls').fetchone())
        jobs=[dict(r) for r in db.execute('''SELECT ticket,ordinal,attempt,state,created,started,finished,
                                            encoding,payload_hash,prompt_hash FROM ipad_jobs ORDER BY ordinal,attempt''')]
        events=[dict(r) for r in db.execute('SELECT time,ticket,action FROM ipad_events ORDER BY id DESC LIMIT 30')]
    grades=[]
    path=Path(directory)/'ipad-judging.sqlite3'
    if path.exists():
        with contextlib.closing(campaign.connect(path)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='grades'").fetchone():
                grades=[dict(r) for r in db.execute('SELECT attempt_id,status,estimated_cost_usd,grade FROM grades')]
    done=[g for g in grades if g['status']=='done']
    return {'device':DEVICE,'model':MODEL,'protocol':PROTOCOL,'control':c,'jobs':jobs,'events':events,
            'saved':sum(j['state']=='done' for j in jobs),'grades':[{k:v for k,v in g.items() if k!='grade'} for g in grades],
            'judged':len(done),'correct':sum(json.loads(g['grade'])['correct']=='yes' for g in done),
            'output_tokens':None,'reasoning_tokens':None,'ttft_ms':None,
            'scope':'iPad stratum only; not pooled with Mac scores. Round-trip timing includes SSH/Shortcut overhead.',
            'image_policy':'Stop at first untouched image; retain frozen sample and coverage gaps.'}


def mirror_gateway(directory, path=None):
    """Metadata-only side table; never changes this Mac's calls or quota holds.

    Existing gateway /monitor intentionally keeps its Mac-only contract. Device
    accounting is available in worker_calls and the iPad control page.
    """
    path=Path(path) if path else cli.TOKEN.with_name('calls.sqlite3')
    if not path.is_file():raise ValueError('gateway ledger missing')
    with queue(directory) as source:
        rows=[dict(r) for r in source.execute('SELECT * FROM ipad_jobs')]
    with contextlib.closing(sqlite3.connect(path,timeout=5)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS worker_calls (
          id TEXT PRIMARY KEY, device TEXT NOT NULL, model TEXT NOT NULL,
          protocol TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL,
          started TEXT, finished TEXT, duration_ms INTEGER, request_bytes INTEGER,
          response_bytes INTEGER, payload_sha256 TEXT, input_tokens INTEGER,
          output_tokens INTEGER, reasoning_tokens INTEGER, ttft_ms INTEGER)""")
        for row in rows:
            duration=(round((dt.datetime.fromisoformat(row['finished'])-dt.datetime.fromisoformat(row['started'])).total_seconds()*1000)
                      if row['started'] and row['finished'] else None)
            db.execute("""INSERT INTO worker_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)
              ON CONFLICT(id) DO UPDATE SET state=excluded.state,started=excluded.started,
              finished=excluded.finished,duration_ms=excluded.duration_ms,response_bytes=excluded.response_bytes""",
              (row['ticket'],DEVICE,MODEL,PROTOCOL,row['state'],row['created'],row['started'],row['finished'],duration,
               len(row['prompt'].encode()),len(base64.b64decode(row['raw_b64'])) if row['raw_b64'] else None,row['payload_hash']))
    return len(rows)


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['enqueue','resume','pause','claim','submit','status','retry','unknown','rate_limited','skip','cancel','grade','start','server'])
    p.add_argument('--directory',type=Path,default=DEFAULT)
    p.add_argument('--count',type=int,default=7)
    p.add_argument('--ticket');p.add_argument('--acknowledge-uncertain',action='store_true')
    p.add_argument('--retry-grade',type=int)
    p.add_argument('--port',type=int,default=1983)
    a=p.parse_args();d=a.directory.resolve()
    try:
        if a.command=='enqueue':result=enqueue(d,a.count)
        elif a.command in ('pause','resume'):result=control(d,a.command=='resume')
        elif a.command=='claim':result=claim(d)
        elif a.command=='submit':result=submit(d,sys.stdin.buffer)
        elif a.command=='status':result=status(d)
        elif a.command=='grade':result={'exit_code':grade(d,a.retry_grade)}
        elif a.command=='server':
            from .ipad_dashboard import serve
            serve(d,a.port);return
        elif a.command=='start':
            with (d/'ipad-server.lock').open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with (d/'ipad-server.log').open('ab',buffering=0) as log:
                proc=subprocess.Popen([sys.executable,'-u','-m','afm_hle.ipad_campaign','server','--directory',str(d),'--port',str(a.port)],
                    cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
            cli.private_json(d/'ipad-process.json',{'pid':proc.pid,'started':cli.stamp()})
            result={'pid':proc.pid,'dashboard':'http://127.0.0.1:'+str(a.port)}
        else:result=resolve(d,a.ticket,a.command,a.acknowledge_uncertain)
        print(json.dumps(result))
    except (ValueError,OSError,sqlite3.Error,subprocess.SubprocessError) as e:
        print(json.dumps({'error':str(e) if isinstance(e,ValueError) else type(e).__name__}),file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':main()
