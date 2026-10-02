"""One synthetic pixel-reading qualification; never allocates an HLE question."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import secrets
import sys
import uuid
from PIL import Image, ImageDraw, ImageFont
from . import cli, ipad_campaign

PATH=ipad_campaign.DEFAULT/'ipad-image-qualification.json'

def prepare(path=PATH):
    if path.exists():raise ValueError('Qualification already exists; preserve prior attempt')
    code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
    im=Image.new('RGB',(480,160),'white')
    ImageDraw.Draw(im).text((35,40),code,font=ImageFont.load_default(size=60),fill='black')
    stream=io.BytesIO();im.save(stream,format='PNG')
    record={'ticket':str(uuid.uuid4()),'state':'queued','created':cli.stamp(),'expected':code,
        'prompt':'Read the six-character code in the attached image. Reply with only the code.',
        'image_base64':base64.b64encode(stream.getvalue()).decode(),'qualified':False}
    cli.private_json(path,record)
    return {'status':'prepared','synthetic':True}

def claim(path=PATH):
    r=json.loads(path.read_text())
    if r['state']!='queued':raise ValueError('Qualification already attempted; no automatic replay')
    r.update(state='inflight',started=cli.stamp());cli.private_json(path,r)
    return {k:r[k] for k in ('ticket','prompt','image_base64')}

def submit(source,path=PATH):
    raw=source.read(ipad_campaign.MAX_RESULT+1)
    if len(raw)>ipad_campaign.MAX_RESULT:raise ValueError('oversize receipt')
    ticket,encoded=raw.decode().split('\n',1)
    r=json.loads(path.read_text())
    if ticket!=r['ticket'] or r['state']!='inflight':raise ValueError('qualification receipt mismatch')
    answer,encoding=ipad_campaign.decode_answer(base64.b64decode(''.join(encoded.split()),validate=True))
    r.update(state='done',finished=cli.stamp(),raw_receipt_b64=base64.b64encode(raw).decode(),
        answer=answer,encoding=encoding,qualified=answer.strip()==r['expected'])
    cli.private_json(path,r)
    return {'status':'recorded','qualified':r['qualified'],'synthetic':True}

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','claim','submit','status']);a=p.parse_args()
    try:
        if a.command=='prepare':r=prepare()
        elif a.command=='claim':r=claim()
        elif a.command=='submit':r=submit(sys.stdin.buffer)
        else:
            data=json.loads(PATH.read_text());r={k:data.get(k) for k in ('state','qualified','created','started','finished')}
        print(json.dumps(r))
    except Exception as e:
        print(json.dumps({'code':type(e).__name__}),file=sys.stderr);raise SystemExit(1)

if __name__=='__main__':main()
