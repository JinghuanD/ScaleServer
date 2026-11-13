"""Shared loopback transport and timeout policy. Never retry application work."""
import threading
import requests

HEALTH_TIMEOUT = (1.0, 2.0)
CONTROL_TIMEOUT = (1.0, 3.0)
UPSTREAM_WORK_TIMEOUT = (1.0, 10.0)
CLIENT_WORK_TIMEOUT = (1.0, 15.0)
STARTUP_WAIT_SECONDS = 45.0
PROTOCOL = 'local-matched-v3'
_local = threading.local()

def session():
    if not hasattr(_local, 'session'):
        s = requests.Session()
        s.trust_env = False
        s.mount('http://', requests.adapters.HTTPAdapter(max_retries=0))
        _local.session = s
    return _local.session

def get(url, **kwargs):
    kwargs.setdefault('allow_redirects', False)
    return session().get(url, **kwargs)

def post(url, **kwargs):
    kwargs.setdefault('allow_redirects', False)
    return session().post(url, **kwargs)

def wait_ready(url, predicate, seconds=STARTUP_WAIT_SECONDS, cancelled=lambda: False):
    import time
    deadline = time.monotonic() + seconds
    last = 'not attempted'
    while time.monotonic() < deadline:
        if cancelled():
            raise RuntimeError('Readiness cancelled: child process exited')
        remaining = deadline - time.monotonic()
        # Each socket timeout is capped by remaining budget (not a strict total deadline).
        timeout = (min(1.0, remaining), min(2.0, remaining))
        try:
            r = get(url, timeout=timeout)
            r.raise_for_status()
            body = r.json()
            if predicate(body): return body
            last = 'Unexpected health body: ' + repr(body)
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
    raise TimeoutError(f'Local readiness failed at {url}; last observation: {last}')
