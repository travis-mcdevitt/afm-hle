"""Detached, checkpoint-driven CoreDevice launcher. Never replays unknown calls."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.parse
from . import cli, ipad_campaign as queue

ROOT=Path(__file__).resolve().parent.parent
STATE=ROOT/'.private/ipad-remote'
UNRESOLVED={'inflight','unknown','failed','rate_limited'}


def launch(device,name):
    url='shortcuts://run-shortcut?name='+urllib.parse.quote(name,safe='')
    result=subprocess.run(['xcrun','devicectl','device','process','launch','--device',device,
        '--payload-url',url,'--timeout','20','com.apple.shortcuts'],capture_output=True,text=True,timeout=30)
    # Keep identifiers and raw Apple diagnostics private; expose only codes.
    if result.returncode:return 'device_launch_failed'
    return None


def step(directory,config,previous,now=None,launcher=launch):
    now=now or dt.datetime.now(dt.timezone.utc)
    s=queue.status(directory)
    summary={'updated_at':now.isoformat(),'saved':s['saved'],'graded':s['judged']}
    jobs=s['jobs'];pending=[j for j in jobs if j['state']=='queued']
    unresolved=[j for j in jobs if j['state'] in UNRESOLVED]
    active=previous.get('launch')
    if active:
        job=next((j for j in jobs if j['ticket']==active['ticket']),None)
        if job and job['state'] in ('done','superseded','skipped','cancelled'):active=None
        else:
            elapsed=(now-dt.datetime.fromisoformat(active['time'])).total_seconds()
            phase='awaiting_upload' if job and job['state']=='inflight' else 'awaiting_claim'
            if elapsed>=600:phase='requires_review'
            return dict(summary,phase=phase,launch=active,code='unresolved_remote_attempt' if phase=='requires_review' else None)
    if unresolved:return dict(summary,phase='requires_review',code='unresolved_attempt')
    if not s['control']['enabled']:return dict(summary,phase='paused')
    if not pending:
        result=queue.enqueue(directory,1)
        if result['boundary']:return dict(summary,phase='image_transport_blocked',ordinal=result['boundary']['ordinal'])
        if not result['queued']:return dict(summary,phase='available_ipad_work_complete')
        pending=[j for j in queue.status(directory)['jobs'] if j['state']=='queued']
    job=min(pending,key=lambda j:(j['ordinal'],j['attempt']))
    # Record intent before crossing the device boundary: a crash must not replay.
    active={'ticket':job['ticket'],'ordinal':job['ordinal'],'time':now.isoformat()}
    state=dict(summary,phase='launching',launch=active)
    cli.private_json(STATE/'state.json',state)
    shortcut='AFM iPad HLE Pro Image One' if job.get('job_protocol')==queue.IMAGE_PROTOCOL else config.get('shortcut','AFM iPad HLE Pro One')
    code=launcher(config['device'],shortcut)
    return dict(summary,phase='requires_review' if code else 'awaiting_claim',launch=active,code=code)


def worker(directory):
    lock=(STATE/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    config=json.loads((STATE/'config.json').read_text())
    previous=json.loads((STATE/'state.json').read_text()) if (STATE/'state.json').exists() else {}
    stop=False
    def shutdown(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    awake=subprocess.Popen(['/usr/bin/caffeinate','-i'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        while not stop:
            try:
                previous=step(directory,config,previous)
                cli.private_json(STATE/'state.json',previous)
                if previous['phase'] in ('requires_review','image_transport_blocked','available_ipad_work_complete','paused'):return
            except Exception as e:
                cli.private_json(STATE/'state.json',dict(previous,phase='requires_review',code=type(e).__name__,updated_at=cli.stamp()))
                return
            for _ in range(15):
                if stop:return
                time.sleep(1)
    finally:awake.terminate();awake.wait();lock.close()


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['start','worker','status'])
    p.add_argument('--directory',type=Path,default=queue.DEFAULT)
    p.add_argument('--device');a=p.parse_args()
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    if a.command=='status':print((STATE/'state.json').read_text());return
    if a.command=='worker':worker(a.directory.resolve());return
    with (STATE/'worker.lock').open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if a.device:cli.private_json(STATE/'config.json',{'device':a.device,'shortcut':'AFM iPad HLE Pro One'})
    if not (STATE/'config.json').exists():raise ValueError('--device required on first start')
    # Keep launch intent across process restarts. It is cleared only after upload.
    with (STATE/'worker.log').open('ab',buffering=0) as log:
        proc=subprocess.Popen([sys.executable,'-u','-m','afm_hle.ipad_remote','worker','--directory',str(a.directory.resolve())],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
    cli.private_json(STATE/'process.json',{'pid':proc.pid,'started_at':cli.stamp()})
    print(json.dumps({'pid':proc.pid,'status_file':str(STATE/'state.json')}))

if __name__=='__main__':main()
