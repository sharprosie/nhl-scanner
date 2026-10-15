"""Quiet hours: between 10 PM and 7 AM Pacific, every phone ping (ntfy) is delivered silently (priority 'low':
no sound, no vibration). Messages still arrive and can be read in the morning. Imported once by each script."""
import datetime as dt, urllib.request
from zoneinfo import ZoneInfo

START, END = 22, 7          # 10 PM to 7 AM, Pacific


def quiet_now():
    h = dt.datetime.now(ZoneInfo('America/Los_Angeles')).hour
    return h >= START or h < END


_orig = urllib.request.urlopen


def _urlopen(req, *a, **k):
    try:
        if isinstance(req, urllib.request.Request) and 'ntfy.sh' in req.full_url and quiet_now():
            for name in list(req.headers):
                if name.lower() == 'priority':
                    del req.headers[name]
            req.add_header('Priority', 'low')
    except Exception:
        pass
    return _orig(req, *a, **k)


if getattr(urllib.request.urlopen, '__name__', '') != '_urlopen':
    urllib.request.urlopen = _urlopen
