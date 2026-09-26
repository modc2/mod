"""cron entrypoint for the once-a-day update.

Prints the day's post (default: Discord markdown) to stdout so cron can append
it to a log, pipe it, or mail it:

    5 9 * * *  /usr/bin/python3 /root/mod/mod/orbit/updates/daily_cron.py >> /tmp/updates-daily.log 2>&1

It deliberately does NOT mark the day posted — that flag means "a human sent
this", and the app's TO POST badge would start lying if a cron flipped it.
Env: UPDATES_STYLE (twitter|discord|markdown), UPDATES_DATE (latest|today|
yesterday|YYYY-MM-DD), UPDATES_REPO, UPDATES_BRANCH.
"""
import os
import sys
from datetime import datetime, timezone

_root = os.path.abspath(__file__)
for _ in range(4):                     # updates -> orbit -> mod(pkg) -> repo root
    _root = os.path.dirname(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

import mod as m  # noqa: E402

up = m.mod('updates')()
text = up.paste(date=os.environ.get('UPDATES_DATE') or 'latest',
                style=os.environ.get('UPDATES_STYLE') or 'discord',
                repo=os.environ.get('UPDATES_REPO') or None,
                branch=os.environ.get('UPDATES_BRANCH') or None)
print(f'=== {datetime.now(timezone.utc).isoformat(timespec="seconds")} ===')
print(text)
