"""Private, metadata-only routing trace for the enrolled SSH worker.

Never reads stdin, question/answer content, keys, usernames, or raw peer addresses.
"""
import hashlib
import json
import os
from pathlib import Path
from .cli import stamp

COMMANDS={'afm-ipad-worker-ping','afm-ipad-hle-pro-next','afm-ipad-hle-pro-result',
          'afm-ipad-pro-check-next','afm-ipad-pro-check-result','afm-ipad-check-next','afm-ipad-check-result'}


def main():
    command=os.environ.get('SSH_ORIGINAL_COMMAND','')
    record={'time':stamp(),'command':command if command in COMMANDS else 'other',
            'connection_hash':hashlib.sha256(os.environ.get('SSH_CONNECTION','').encode()).hexdigest()[:16]}
    path=Path(__file__).resolve().parent.parent/'.private/ipad-ssh-routing.jsonl'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    try:os.write(fd,(json.dumps(record)+'\n').encode())
    finally:os.close(fd)

if __name__=='__main__':main()
