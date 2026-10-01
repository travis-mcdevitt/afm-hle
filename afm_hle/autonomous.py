"""Native Mac campaign supervision: quota cooldowns, bounded recovery, Pro then Cloud."""
import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from . import campaign, cli

ROOT=campaign.ROOT
STATE=ROOT/'.private/autonomous'
UTC=dt.timezone.utc
PACIFIC=ZoneInfo('America/Los_Angeles')


def cooldown_due(denial, duration=86400):
    denial=dt.datetime.fromisoformat(denial)
    later=denial+dt.timedelta(seconds=max(duration,86400))
    tomorrow=dt.datetime.combine(denial.astimezone(PACIFIC).date()+dt.timedelta(days=1),dt.time(),PACIFIC)
    return max(later,tomorrow).astimezone(UTC).isoformat()


def evidence(attempt):
    """Only inspect the narrow, serialized upstream attempt's system-log window."""
    start=dt.datetime.fromisoformat(attempt['started'])-dt.timedelta(seconds=1)
    end=dt.datetime.fromisoformat(attempt['finished'])+dt.timedelta(seconds=1)
    predicate='(process == "PrivateMLClientInferenceProviderService" OR process == "BackgroundShortcutRunner" OR process == "privatecloudcomputed") AND (eventMessage CONTAINS[c] "recitation rejection" OR eventMessage CONTAINS[c] "deniedDueToUserDeviceRateLimit" OR eventMessage CONTAINS[c] "rate limit applied")'
    result=subprocess.run(['/usr/bin/log','show','--start',start.astimezone(PACIFIC).strftime('%Y-%m-%d %H:%M:%S'),'--end',end.astimezone(PACIFIC).strftime('%Y-%m-%d %H:%M:%S'),'--style','compact','--predicate',predicate],capture_output=True,text=True,timeout=20)
    if result.returncode:return None
    if 'deniedDueToUserDeviceRateLimit' in result.stdout:
        durations=re.findall(r'duration=([0-9.]+)',result.stdout)
        return ('quota',max([86400]+[int(float(x)) for x in durations]))
    if 'recitation rejection' in result.stdout.lower():return ('recitation',0)
    return None


def emit(kind, **fields):
    print(json.dumps(dict(time=cli.stamp(),event=kind,**fields)),flush=True)


def command(*args):
    result=subprocess.run([sys.executable,'-m',*args],cwd=ROOT,capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('control command failed: '+args[0])
    return result.stdout


def stop_worker(directory):
    path=directory/'process.json'
    if not path.exists():return
    pid=json.loads(path.read_text())['pid']
    proc=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
    if 'afm_hle.campaign worker' not in proc.stdout:return
    command('afm_hle.campaign','stop','--directory',str(directory))
    for _ in range(20):
        try:
            with (directory/'worker.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return
        except BlockingIOError:time.sleep(1)
    raise RuntimeError('worker did not finish checkpointing')


def start_worker(directory):
    stop_worker(directory)
    command('afm_hle.campaign','start','--directory',str(directory))


def quota_record(directory,model,attempt,duration=86400):
    record={'model':model,'denied_at':attempt['finished'],'probe_not_before':cooldown_due(attempt['finished'],duration),'status':'cooldown','evidence':'confirmed_provider_quota'}
    cli.private_json(directory/'quota-recovery-state.json',record)
    campaign.set_phase(directory,'quota_wait','Autonomous recovery scheduled at '+record['probe_not_before']+'; no inference during cooldown.')
    emit('quota_wait',model=model,next_probe=record['probe_not_before'])


def unresolved(directory,model):
    plan=campaign.load_plan(directory)
    with contextlib.closing(campaign.connect(directory/'generation.sqlite3')) as db:
        rows=[dict(r) for r in db.execute("SELECT * FROM items WHERE model=? AND status IN ('unknown','paused','blocked','inflight')",(model,)) if r['qid'] in plan['selected_ids']]
        for row in rows:
            a=db.execute('SELECT * FROM attempts WHERE qid=? AND model=? ORDER BY id DESC LIMIT1'.replace('LIMIT1','LIMIT 1'),(row['qid'],model)).fetchone()
            row['attempt']=dict(a) if a else None
            row['retries']=db.execute("SELECT count(*) FROM events WHERE qid=? AND model=? AND action='autonomous_retry'",(row['qid'],model)).fetchone()[0]
    return rows


def resolve(directory,row,action,reason):
    with cli.state(directory/'generation.sqlite3') as db:
        with db:db.execute('INSERT INTO events VALUES(?,?,?,?)',(cli.stamp(),row['qid'],row['model'],reason))
    cli.resolve(SimpleNamespace(db=directory/'generation.sqlite3',id=row['qid'],model=row['model'],action=action))


def tick(directory,model,now=None):
    now=now or dt.datetime.now(UTC)
    s=campaign.stats(directory)
    counts=s['models'][model]['generation']
    quota_path=directory/'quota-recovery-state.json'
    quota=json.loads(quota_path.read_text()) if quota_path.exists() else None
    if quota and quota['status']=='cooldown':
        due=dt.datetime.fromisoformat(quota['probe_not_before'])
        if s['phase']!='quota_wait':campaign.set_phase(directory,'quota_wait','Autonomous recovery scheduled at '+quota['probe_not_before'])
        if now<due:return False
        stop_worker(directory)
        # Clear only this route's operational hold, then probe one real pending HLE item.
        subprocess.run([sys.executable,str(ROOT.parent/'afm-gateway/gateway.py'),'resume','--model',model],check=True,capture_output=True,timeout=15)
        rows=unresolved(directory,model)
        for row in rows:resolve(directory,row,'retry','autonomous_quota_probe: original attempt preserved')
        plan=campaign.load_plan(directory)
        args=SimpleNamespace(db=directory/'generation.sqlite3',data=Path(plan['data_path']),endpoint='http://127.0.0.1:1979',token_file=cli.TOKEN,max_calls=1,max_samples=plan['sample_size'],models=[model])
        code=cli.run(args)
        if code:
            rows=unresolved(directory,model)
            a=rows[0]['attempt'] if rows else None
            ev=evidence(a) if a else None
            if a and (a['code']=='rate_limited' or (ev and ev[0]=='quota')):
                quota_record(directory,model,a,ev[1] if ev else 86400);return False
            quota['status']='probe_failed';cli.private_json(quota_path,quota)
            emit('quota_probe_other_failure',model=model)
        else:
            quota['status']='recovered';quota['recovered_at']=cli.stamp();cli.private_json(quota_path,quota)
            emit('quota_recovered',model=model)
            start_worker(directory);return False
    s=campaign.stats(directory);counts=s['models'][model]['generation']
    if s['phase'] in ('generating','grading','retry_wait','grading_retry_wait'):return False
    rows=unresolved(directory,model)
    if rows:
        stop_worker(directory)
        for row in rows:
            a=row['attempt']
            if not a or not a['finished']:
                emit('requires_review',model=model,code='interrupted_unknown');return False
            ev=evidence(a) if a['code']=='shortcut_failed' else None
            if a['code'] in ('rate_limited','local_quota_hold') or (ev and ev[0]=='quota'):
                quota_record(directory,model,a,ev[1] if ev else 86400);return False
            if ev and ev[0]=='recitation':
                resolve(directory,row,'skip','autonomous_gap: confirmed provider recitation rejection; attempt preserved')
                emit('coverage_gap',model=model,code='recitation_rejection');continue
            if row['retries']>=5 or row['status']=='blocked':
                resolve(directory,row,'skip','autonomous_gap: '+str(a['code'])+'; bounded retry exhaustion or permanent input failure; attempt preserved for review')
                emit('coverage_gap',model=model,code=a['code']);continue
            if a['code'] not in campaign.TRANSIENT:
                emit('requires_review',model=model,code=a['code']);return False
            due=dt.datetime.fromisoformat(a['finished'])+dt.timedelta(seconds=min(15*2**row['retries'],240))
            if now<due:return False
            resolve(directory,row,'retry','autonomous_retry')
        start_worker(directory);return False
    if counts.get('pending',0):start_worker(directory);return False
    # Mac coverage is complete with explicitly reported gaps; device jobs remain separate.
    if s['grading_pending']:
        if not s['judge_errors']:start_worker(directory);return False
        if s.get('grading_recovery',{}).get('available',0):start_worker(directory);return False
    campaign.set_phase(directory,'finished_with_gaps' if any(counts.get(k,0) for k in ('skipped','device_done','device_reserved')) else 'complete','Mac sample exhausted; device-owned results excluded and unresolved reservations remain coverage gaps. Grading failures, if any, retained.')
    emit('model_finished',model=model,saved=counts.get('done',0),graded=s['models'][model]['judged'],coverage=counts)
    return True


def worker():
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=(STATE/'supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stop=False
    def shutdown(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    awake=subprocess.Popen(['/usr/bin/caffeinate','-i'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        while not stop:
            try:
                pro=ROOT/'.private/campaign';cloud=ROOT/'.private/campaign-cloud'
                done=tick(pro,'afm-cloud-pro')
                if done and tick(cloud,'afm-cloud'):
                    cli.private_json(STATE/'state.json',{'phase':'complete','updated_at':cli.stamp()});emit('all_mac_work_finished');return
                cli.private_json(STATE/'state.json',{'phase':'supervising','active_model':'afm-cloud' if done else 'afm-cloud-pro','updated_at':cli.stamp()})
            except Exception as e:
                emit('supervisor_error',code=type(e).__name__)
                cli.private_json(STATE/'state.json',{'phase':'retrying_supervisor','code':type(e).__name__,'updated_at':cli.stamp()})
            for _ in range(60):
                if stop:return
                time.sleep(1)
    finally:awake.terminate();awake.wait()


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('command',choices=['start','worker','status']);a=p.parse_args()
    if a.command=='worker':worker()
    elif a.command=='status':print((STATE/'state.json').read_text())
    else:
        STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (STATE/'supervisor.lock').open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with (STATE/'supervisor.log').open('ab',buffering=0) as log:
            proc=subprocess.Popen([sys.executable,'-u','-m','afm_hle.autonomous','worker'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        cli.private_json(STATE/'process.json',{'pid':proc.pid,'started_at':cli.stamp()})
        print('Detached native supervisor started:',proc.pid)

if __name__=='__main__':main()
