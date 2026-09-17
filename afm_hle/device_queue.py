"""Offline durable queue for future gateway-connected iPad workers.

No HTTP listener, dispatch loop, or live benchmark integration is enabled here.
Jobs remain in flight until a result or explicit operator resolution is recorded.
"""
import contextlib
import json
import sqlite3
from pathlib import Path
from .cli import digest, encode, stamp, MODELS


class DeviceQueue:
    def __init__(self, path):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(path,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, device TEXT NOT NULL, model TEXT NOT NULL,
            payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
            state TEXT NOT NULL, result TEXT, created TEXT NOT NULL, started TEXT,
            finished TEXT);
          CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, job TEXT NOT NULL, time TEXT NOT NULL,
            action TEXT NOT NULL);
        ''')
        path.chmod(0o600)

    def close(self):self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def enqueue(self, job_id, device, body):
        """Coordinator supplies stable id and input-only chat payload, never a row of HLE data."""
        if not isinstance(job_id,str) or not job_id or not isinstance(device,str) or not device:
            raise ValueError('job and device identifiers required')
        if set(body)!={'model','stream','messages'} or body['model'] not in MODELS or body['stream'] is not False:
            raise ValueError('input-only nonstreaming cloud payload required')
        if not isinstance(body['messages'],list) or not body['messages']:
            raise ValueError('messages required')
        hashed=digest(body)
        with self.transaction():
            old=self.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if old:
                if old['payload_hash']!=hashed or old['device']!=device:
                    raise ValueError('job identity conflict')
                return
            self.db.execute('INSERT INTO jobs(id,device,model,payload,payload_hash,state,created) VALUES(?,?,?,?,?,?,?)',
                (job_id,device,body['model'],encode(body).decode(),hashed,'pending',stamp()))
            self.db.execute('INSERT INTO events(job,time,action) VALUES(?,?,?)',(job_id,stamp(),'enqueued'))

    def claim(self, device):
        """device must be derived from authenticated credentials by the future gateway."""
        with self.transaction():
            if self.db.execute("SELECT 1 FROM jobs WHERE device=? AND state IN ('inflight','unknown','rate_limited','failed')",(device,)).fetchone():
                return None
            row=self.db.execute("SELECT * FROM jobs WHERE device=? AND state='pending' ORDER BY created,id LIMIT 1",(device,)).fetchone()
            if not row:return None
            self.db.execute("UPDATE jobs SET state='inflight',started=? WHERE id=?",(stamp(),row['id']))
            self.db.execute('INSERT INTO events(job,time,action) VALUES(?,?,?)',(row['id'],stamp(),'claimed'))
            return {'job_id':row['id'],'device_id':device,'payload_sha256':row['payload_hash'],
                    'request':json.loads(row['payload'])}

    def finish(self, device, job_id, payload_hash, result):
        if set(result)!={'status','model','text','error_code'}:
            raise ValueError('invalid result fields')
        if result['status'] not in ('done','unknown','rate_limited','failed'):
            raise ValueError('invalid result status')
        if result['status']=='done':
            if not isinstance(result['text'],str) or not result['text'].strip() or result['error_code'] is not None:
                raise ValueError('successful answer required')
        elif result['text'] is not None or result['error_code'] not in ('shortcut_timeout','shortcut_failed','rate_limited','worker_interrupted'):
            raise ValueError('sanitized failure required')
        serialized=encode(result).decode()
        with self.transaction():
            row=self.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['device']!=device or row['payload_hash']!=payload_hash or row['model']!=result['model']:
                raise ValueError('result provenance mismatch')
            if row['result'] is not None:
                if row['result']!=serialized:raise ValueError('conflicting result')
                return  # A lost HTTP acknowledgement may safely be submitted again.
            if row['state']!='inflight':raise ValueError('job was not dispatched')
            self.db.execute('UPDATE jobs SET state=?,result=?,finished=? WHERE id=?',
                (result['status'],serialized,stamp(),job_id))
            self.db.execute('INSERT INTO events(job,time,action) VALUES(?,?,?)',(job_id,stamp(),result['status']))
