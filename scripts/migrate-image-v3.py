"""Audited v2-to-v3 migration; preserves every successfully dispatched payload."""
import base64
import io
import json
import sqlite3
from pathlib import Path
from afm_hle import cli
from afm_hle.images import POLICY

OLD_POLICY='inline-png-jpeg;static-webp-gif-to-rgba-png-v1'

def legacy_image(value):
    if value.startswith(('data:image/png;base64,','data:image/jpeg;base64,')):return value
    from PIL import Image
    with Image.open(io.BytesIO(base64.b64decode(value.split(',',1)[1],validate=True))) as im:
        out=io.BytesIO();im.convert('RGBA').save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()

def migrate(directory):
    directory=Path(directory)
    plan=json.loads((directory/'plan.json').read_text())
    data=json.loads(Path(plan['data_path']).read_text())
    questions={q['id']:q for q in data['questions']}
    paths=[directory/'generation.sqlite3',directory/'judging.sqlite3']
    records=[]
    for path in paths:
        with cli.state(path) as db:
            old=json.loads(db.execute('SELECT manifest FROM config WHERE id=1').fetchone()[0])
            if old['protocol']=='afm-hle-v3':continue
            assert old['protocol']=='afm-hle-v2' and old['image_policy']==OLD_POLICY
            assert old['dataset_sha256']==cli.digest(data)
            assert not db.execute("SELECT 1 FROM attempts WHERE status='inflight'").fetchone()
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='grades'").fetchone():
                assert not db.execute("SELECT 1 FROM grades WHERE status='inflight'").fetchone()
            changed=[]
            for a in db.execute('SELECT id,qid,model,status,code FROM attempts'):
                q=questions[a['qid']]
                new_payload=cli.payload(q,a['model'])
                old_payload=json.loads(cli.encode(new_payload))
                if q.get('image'):old_payload['messages'][1]['content'][1]['image_url']['url']=legacy_image(q['image'])
                if new_payload!=old_payload:
                    assert a['status']=='blocked' and a['code']=='invalid_image', 'Previously dispatched payload would change'
                    changed.append({'attempt_id':a['id'],'qid':a['qid'],'old_payload_sha256':cli.digest(old_payload),'new_payload_sha256':cli.digest(new_payload)})
            backup=path.with_name(path.stem+'.before-image-v3.sqlite3')
            assert not backup.exists()
            with sqlite3.connect(backup) as dst:db.backup(dst)
            backup.chmod(0o600)
            new=dict(old,protocol='afm-hle-v3',image_policy=POLICY)
            record={'time':cli.stamp(),'old_manifest':old,'new_manifest':new,'changed_blocked_attempts':changed,'successful_payloads_unchanged':True}
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS protocol_history (time TEXT, record TEXT)')
                judge_row=None
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='judge_config'").fetchone():
                    judge_row=db.execute('SELECT manifest FROM judge_config WHERE id=1').fetchone()
                if judge_row:
                    spec=json.loads(judge_row[0]);record['old_judge_manifest']=spec.copy()
                    assert spec['generation_manifest_sha256']==cli.digest(old)
                    spec['generation_manifest_sha256']=cli.digest(new)
                    db.execute('UPDATE judge_config SET manifest=? WHERE id=1',(cli.encode(spec).decode(),))
                db.execute('INSERT INTO protocol_history VALUES(?,?)',(record['time'],cli.encode(record).decode()))
                db.execute('UPDATE config SET manifest=? WHERE id=1',(cli.encode(new).decode(),))
                db.execute('INSERT INTO events VALUES(?,?,?,?)',(record['time'],None,None,'migrate_image_v3: successful payloads unchanged; original manifests in protocol_history and backup'))
            records.append(record)
    cli.private_json(directory/'image-v3-migration.json',records)
    print(directory,': migrated',len(records),'databases; successful payloads unchanged')

if __name__=='__main__':
    import sys
    for directory in sys.argv[1:]:migrate(directory)
