"""Fail closed when cloud secrets are missing; never fall back to ephemeral SQLite."""
import os
import sys
from urllib.parse import urlsplit


def validate_environment(env):
    required = ('DATABASE_URL', 'OANDA_ACCOUNT_ID', 'OANDA_TOKEN')
    missing = [key for key in required if not env.get(key, '').strip()]
    if missing:
        raise ValueError('Missing required settings: ' + ', '.join(missing))
    try:
        url = urlsplit(env['DATABASE_URL'])
        valid = url.scheme in ('postgres', 'postgresql') and bool(url.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('DATABASE_URL must be a PostgreSQL connection URI')


if __name__ == '__main__':
    try:
        validate_environment(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, 'bot.py', 'run'])
