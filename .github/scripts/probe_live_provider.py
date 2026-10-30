"""One bounded acquisition probe. Never prints token or request URL."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def probe():
    token = os.environ['BETSAPI_KEY']
    query = urllib.parse.urlencode(dict(token=token, sport_id=1, per_page=100))
    request = urllib.request.Request('https://api.b365api.com/v3/events/inplay?' + query,
                                     headers={'User-Agent':'dzam-live-tb/2.0'})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.load(response)
            result = dict(status=response.status, success=data.get('success'),
                          events=len(data.get('results') or []),
                          remaining=response.headers.get('X-RateLimit-Remaining'))
    except urllib.error.HTTPError as exc:
        result = dict(status=exc.code, reason=str(exc.reason),
                      body=exc.read(500).decode('utf-8','replace').replace(token,'[redacted]'))
    except Exception as exc:
        result = dict(error_type=type(exc).__name__)
    result['seconds'] = round(time.monotonic()-started,3)
    print('LIVE_PROVIDER_PROBE=' + json.dumps(result), flush=True)


if __name__ == '__main__':
    probe()
