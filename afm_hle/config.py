"""Read local connection settings without shell evaluation or logging secrets."""
from pathlib import Path
import shlex


def read_env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, sep, value = line.partition('=')
        if not sep or not key.strip().isidentifier():
            raise ValueError('invalid environment assignment')
        parts = shlex.split(value, comments=True, posix=True)
        if len(parts) > 1:
            raise ValueError('quote environment values containing spaces')
        values[key.strip()] = parts[0] if parts else ''
    for key in ('OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENAI_API_KEY'):
        if not values.get(key):
            raise ValueError('required connection setting missing')
    return values
