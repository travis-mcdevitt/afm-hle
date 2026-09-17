"""Loopback-only iPad controls and detached grading. No model polling."""
import fcntl
import io
import json
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import ipad_campaign as worker

HTML=r'''<!doctype html><meta charset="utf-8"><title>iPad AFM Pro worker</title>
<style>body{font:16px system-ui;background:#10151d;color:#eef2f6;max-width:1100px;margin:35px auto;padding:0 24px}button{padding:9px;margin:4px;border:0;border-radius:6px;cursor:pointer}a{color:#6fe0c1}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #394451;padding:10px}small{color:#abb8c9}.card{background:#1d2633;padding:22px;border-radius:12px;margin:20px 0}#error{color:#ffb2a7}</style>
<h1>iPad · AFM Cloud Pro</h1><a href="http://127.0.0.1:1981/">Mac benchmark dashboard</a>
<p>Start <b>AFM iPad HLE Pro</b> on the iPad to pull work. Mac controls apply at the next pull. Pause cannot cancel an Apple request already in progress.</p>
<div class="card"><h2 id="state">Connecting…</h2><p id="counts"></p>
<button onclick="act('resume')">Resume queue</button><button onclick="act('pause')">Pause queue</button>
<button onclick="act('enqueue',{count:7})">Queue next 7</button><button onclick="act('grade')">Grade saved answers</button>
<p id="detail"></p><small>Stops at the first untouched image question until image transport is qualified. No automatic generation retries. Grades are separate from Mac results.</small></div>
<p><label>Retry a saved receipt upload from this Mac: <input id="receipt" type="file" accept=".txt"></label> <button onclick="upload()">Upload receipt (no model call)</button></p><p id="error"></p><p id="grading"></p><table><thead><tr><th>Sample #</th><th>Attempt</th><th>State</th><th>Round trip</th><th>Controls</th></tr></thead><tbody id="jobs"></tbody></table>
<h2>Recent events</h2><pre id="events"></pre><script>
const csrf='__TOKEN__';const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function act(action,extra={}){try{const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','X-Worker-Control':csrf},body:JSON.stringify({action,...extra})});const x=await r.json();if(!r.ok)throw Error(x.error);document.getElementById('error').textContent=x.boundary?'Queued '+x.queued+'; stopped at sample #'+x.boundary.ordinal+' (image transport awaiting qualification).':'';await refresh()}catch(e){document.getElementById('error').textContent=e.message}}
async function upload(){const f=document.getElementById('receipt').files[0];if(!f)return;await act('upload',{receipt:await f.text()})}
function resolve(ticket,action){let acknowledge=false;if(['retry','skip'].includes(action)){acknowledge=confirm('Stop the iPad shortcut first. The previous call may have consumed quota. Retry creates a NEW attempt; upload an existing saved receipt instead if available. Continue?');if(!acknowledge)return}act(action,{ticket,acknowledge})}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('Status unavailable');const s=await r.json();document.getElementById('state').textContent=s.control.enabled?'Queue armed':'Queue paused';document.getElementById('counts').textContent=s.saved+' answers saved · '+s.judged+' graded · '+s.correct+' correct';document.getElementById('detail').textContent=s.control.detail+' · Last iPad pull: '+(s.control.heartbeat||'none')+' · '+s.gateway_sync;document.getElementById('jobs').innerHTML=s.jobs.map(j=>{const unresolved=['inflight','unknown','failed','rate_limited'].includes(j.state);const actions=unresolved?['unknown','rate_limited','retry','skip']:j.state==='queued'?['cancel']:[];return '<tr><td>'+j.ordinal+'</td><td>'+j.attempt+'</td><td>'+esc(j.state)+'</td><td>'+(j.started&&j.finished?((new Date(j.finished)-new Date(j.started))/1000).toFixed(1)+'s':'—')+'</td><td>'+actions.map(a=>'<button onclick="resolve(\''+j.ticket+'\',\''+a+'\')">'+esc(a)+'</button>').join('')+'</td></tr>'}).join('');document.getElementById('events').textContent=s.events.map(e=>e.time+' '+e.action).join('\n');document.getElementById('grading').innerHTML='Grading: '+esc(s.grading_service)+' '+s.grades.filter(g=>!['done','inflight'].includes(g.status)).map(g=>'<button onclick="act(\'grade\',{retry_grade:'+g.attempt_id+'})">Retry grade '+g.attempt_id+'</button>').join('')}catch(e){document.getElementById('error').textContent=e.message}}
refresh();setInterval(refresh,3000);
</script>'''


def serve(directory,port):
    lock=(directory/'ipad-server.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    token=secrets.token_urlsafe(32);stop=threading.Event();grade_lock=threading.Lock()
    grade_state={'text':'Idle','gateway':'Pending'}
    def grade(retry=None):
        if not grade_lock.acquire(False):return
        try:
            grade_state['text']='Running'
            code=worker.grade(directory,retry)
            grade_state['text']='Idle' if code==0 else 'Failure retained for explicit retry'
        except Exception as e:grade_state['text']='Deferred: '+type(e).__name__
        finally:grade_lock.release()
    def automatic_grades():
        while not stop.wait(5):
            # Local checkpoint scan only. judge.run calls only ungraded saved
            # answers; failure rows prevent implicit retries.
            try:
                worker.mirror_gateway(directory)
                grade_state['gateway']='Synced to gateway worker_calls (Mac /monitor remains Mac-only)'
            except Exception as e:grade_state['gateway']='Pending: '+type(e).__name__
            grade()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,body,html=False):
            raw=body.encode() if html else json.dumps(body).encode()
            self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{port}'
        def do_GET(self):
            if not self.valid_host():self.reply(403,{'error':'invalid host'});return
            try:
                if self.path=='/':self.reply(200,HTML.replace('__TOKEN__',token),True)
                elif self.path=='/api/status':
                    s=worker.status(directory);s['grading_service']=grade_state['text'];s['gateway_sync']=grade_state['gateway'];self.reply(200,s)
                else:self.reply(404,{'error':'not found'})
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:self.reply(503,{'error':'checkpoint temporarily busy'})
        def do_POST(self):
            if (not self.valid_host() or self.path!='/api/control' or
                not secrets.compare_digest(self.headers.get('X-Worker-Control',''),token) or
                self.headers.get('Origin',f'http://127.0.0.1:{port}')!=f'http://127.0.0.1:{port}'):
                self.reply(403,{'error':'invalid control request'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=worker.MAX_RESULT+8192:raise ValueError('invalid request size')
                data=json.loads(self.rfile.read(size));a=data['action']
                if a=='upload':result=worker.submit(directory,io.BytesIO(data['receipt'].encode('utf-8')))
                elif a in ('pause','resume'):result=worker.control(directory,a=='resume')
                elif a=='enqueue':result=worker.enqueue(directory,data.get('count',7))
                elif a=='grade':
                    threading.Thread(target=grade,args=(data.get('retry_grade'),),daemon=True).start();result={'status':'grading requested'}
                else:result=worker.resolve(directory,data.get('ticket'),a,data.get('acknowledge') is True)
                self.reply(200,result)
            except ValueError as e:self.reply(409,{'error':str(e)})
            except Exception:self.reply(503,{'error':'checkpoint busy; retry control later'})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.timeout=1
    signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
    threading.Thread(target=automatic_grades,daemon=True).start()
    try:
        while not stop.is_set():server.handle_request()
    finally:server.server_close();lock.close()
