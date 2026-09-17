"""Synthetic-only iPad qualification over an authenticated SSH stdin channel.

Never executes a model on the Mac. The iPad's Shortcut performs the model call.
This adapter does not allocate HLE questions or modify running benchmark DBs.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
from . import cli
from .device_queue import DeviceQueue

ROOT=Path(__file__).resolve().parent.parent
DEFAULT=ROOT/'.private/ipad-qualification.sqlite3'
MAX_MESSAGE=1024*1024
CHECK_PROMPT='Reply exactly "TEST_OK"'


def prepare(db,device,model):
    job_id='qualification-v1:'+device+':'+model
    body={'model':model,'stream':False,'messages':[{'role':'user','content':CHECK_PROMPT}]}
    db.enqueue(job_id,device,body)
    return {'job_id':job_id,'status':'prepared','synthetic':True}


def dispatch(db,device,message):
    if not isinstance(message,dict):raise ValueError('JSON object required')
    if message=={'operation':'ping'}:
        with db.transaction():
            db.db.execute('INSERT INTO events(job,time,action) VALUES(?,?,?)',
                ('connection:'+device,cli.stamp(),'ssh_connection_verified'))
        return {'status':'ready','device':device,'model_called':False}
    if message=={'operation':'next'}:
        # Reject unsupported requests before claiming so setup errors cannot
        # strand a real HLE job. This transport version supports synthetic only.
        for row in db.db.execute("SELECT payload FROM jobs WHERE device=? AND state='pending'",(device,)):
            body=json.loads(row['payload'])
            expected={'model':body['model'],'stream':False,'messages':[{'role':'user','content':CHECK_PROMPT}]}
            if body!=expected:raise ValueError('qualification payload required')
        job=db.claim(device)
        if job is None:return {'status':'no_work_or_held'}
        return {'status':'job','job_id':job['job_id'],'payload_sha256':job['payload_sha256'],
                'model':job['request']['model'],'prompt':CHECK_PROMPT,'synthetic':True}
    required={'operation','job_id','payload_sha256','model','status','text','error_code'}
    if set(message)!=required or message['operation']!='result':raise ValueError('invalid operation')
    db.finish(device,message['job_id'],message['payload_sha256'],
        {key:message[key] for key in ('model','status','text','error_code')})
    answer=message['text'] if message['status']=='done' else ''
    decoded=answer
    encoding='plain'
    if answer.startswith('{\\rtf'):
        try:
            decoded=subprocess.run(['/usr/bin/textutil','-convert','txt','-format','rtf','-stdin','-stdout'],
                input=answer,text=True,capture_output=True,check=True,timeout=10).stdout.strip()
            encoding='rtf'
        except (OSError,subprocess.SubprocessError):
            encoding='rtf_decode_failed'
    # Preserve the original upload in the ledger; decoding only affects this
    # synthetic qualification receipt, never a benchmark answer.
    return {'status':'recorded','job_id':message['job_id'],
            'exact_match':decoded=='TEST_OK','raw_exact_match':answer=='TEST_OK',
            'transport_encoding':encoding}


def serve(db,device,source):
    raw=source.read(MAX_MESSAGE+1)
    if len(raw)>MAX_MESSAGE:raise ValueError('message too large')
    return dispatch(db,device,json.loads(raw))


def check_next(db,device,model="afm-cloud"):
    if model not in cli.MODELS:raise ValueError("unsupported model")
    rows=db.db.execute("SELECT id,model FROM jobs WHERE device=? AND state='pending'",(device,)).fetchall()
    expected='qualification-v1:'+device+':'+model
    if len(rows)!=1 or rows[0]['id']!=expected or rows[0]['model']!=model:
        raise ValueError('single matching qualification job required')
    result=dispatch(db,device,{'operation':'next'})
    if result['status']!='job':raise ValueError('qualification already attempted or held')
    return result['prompt']


def check_result(db,device,source,model="afm-cloud"):
    if model not in cli.MODELS:raise ValueError("unsupported model")
    raw=source.read(MAX_MESSAGE+1)
    if len(raw)>MAX_MESSAGE:raise ValueError('answer too large')
    answer=raw.decode('utf-8')
    ident='qualification-v1:'+device+':'+model
    row=db.db.execute('SELECT payload_hash FROM jobs WHERE id=? AND device=?',(ident,device)).fetchone()
    if not row:raise ValueError('qualification missing')
    return dispatch(db,device,{'operation':'result','job_id':ident,
        'payload_sha256':row['payload_hash'],'model':model,
        'status':'done','text':answer,'error_code':None})


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare-check','serve','status','check-next','check-result'])
    p.add_argument('--db',type=Path,default=DEFAULT)
    p.add_argument('--device',default='ipad-air-m3')
    p.add_argument('--model',choices=cli.MODELS,default='afm-cloud')
    args=p.parse_args()
    queue=DeviceQueue(args.db)
    try:
        if args.command=='check-next':
            print(check_next(queue,args.device,args.model),end='')
            return
        elif args.command=='check-result':result=check_result(queue,args.device,sys.stdin.buffer,args.model)
        elif args.command=='prepare-check':result=prepare(queue,args.device,args.model)
        elif args.command=='serve':result=serve(queue,args.device,sys.stdin.buffer)
        else:
            result={'device':args.device,'jobs':[dict(r) for r in queue.db.execute(
                'SELECT id,model,state,created,started,finished FROM jobs WHERE device=?',(args.device,))]}
        print(json.dumps(result))
    except Exception as e:
        # No prompts, credentials, or provider diagnostics in failures.
        print(json.dumps({'error':type(e).__name__}),file=sys.stderr)
        raise SystemExit(1)
    finally:queue.close()


if __name__=='__main__':main()
