"""Loopback-only LB with admission/drain/inflight tracking; no request retries."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import requests
import local_http as http
import logging
import json
from pathlib import Path
from flask import Flask, Response, request
app=Flask(__name__)
BACKENDS=['http://127.0.0.1:'+str(p) for p in (5000,5001,5002)]
lock=threading.RLock(); healthy=[]; draining=set(); inflight={b:0 for b in BACKENDS}; index=0

def check(b):
    try:return http.get(b+'/health',timeout=http.HEALTH_TIMEOUT).status_code==200
    except requests.RequestException:return False

def monitor():
    global healthy
    with ThreadPoolExecutor(max_workers=3) as pool:
        while True:
            found=[b for b,ok in zip(BACKENDS,pool.map(check,BACKENDS)) if ok]
            with lock:healthy=found
            time.sleep(1)

@app.get('/health')
def health():
    with lock:return dict(protocol=http.PROTOCOL, backends=[b for b in healthy if b not in draining],draining=sorted(draining),inflight=dict(inflight))

@app.post('/control/<op>')
def control(op):
    b=(request.get_json(silent=True) or {}).get('backend')
    if b not in BACKENDS or op not in ('drain','undrain'):return {'error':'invalid control'},400
    with lock:
        if op=='drain':draining.add(b)
        else:draining.discard(b)
    return {'ok':True}

@app.get('/work')
def work():
    global index
    with lock:
        available=[b for b in healthy if b not in draining]
        if not available:return {'error':'No healthy backend'},503
        b=available[index%len(available)];index+=1;inflight[b]+=1
    try:
        r=http.get(b+'/work',timeout=http.UPSTREAM_WORK_TIMEOUT)
        return Response(r.content,status=r.status_code,content_type=r.headers.get('Content-Type','application/json'))
    except requests.RequestException as e:return {'error':str(e)},502
    finally:
        with lock:inflight[b]-=1
if __name__=='__main__':
    # No per-request stderr writes: a blocked Windows console can otherwise block handlers.
    logging.getLogger('werkzeug').disabled=True
    print('Local LB v3 listening on http://127.0.0.1:8000; per-request console logs disabled', flush=True)
    threading.Thread(target=monitor,daemon=True).start()
    app.run(host='127.0.0.1',port=8000,threaded=True,use_reloader=False)
