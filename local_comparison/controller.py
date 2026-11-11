"""Matched local polling/event experiment. Python 3.9+, Docker CLI, requests."""
import argparse
import json
import math
import queue
import subprocess
import threading
import time
from pathlib import Path
import requests
import local_http as http

NAMES = ['app1', 'app2', 'app3']
URLS = {n: 'http://127.0.0.1:' + str(5000+i) for i,n in enumerate(NAMES)}
LB = 'http://127.0.0.1:8000'

def docker(*args):
    r = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=45)
    if r.returncode: raise RuntimeError(r.stderr.strip())
    return r.stdout

def running():
    return sorted(n for n in docker('ps','--format','{{.Names}}').splitlines() if n in NAMES)

def sample():
    before = running()
    values = {}
    for line in docker('stats','--no-stream','--format','{{.Name}},{{.CPUPerc}}').splitlines():
        name, value = line.split(',',1)
        if name in before: values[name] = float(value.rstrip('%'))
    after = running()
    valid = before == after and bool(before) and all(n in values and math.isfinite(values[n]) and values[n]>=0 for n in before)
    return (sum(values[n] for n in before)/len(before) if valid else None), after

class Confirmation:
    def __init__(self, high=70, low=30, high_n=5, low_n=10):
        self.high,self.low,self.high_n,self.low_n=high,low,high_n,low_n
        self.reset()
    def reset(self):
        self.direction=None; self.count=0; self.first=None
    def update(self, cpu, stamp):
        direction = 'scale_out' if cpu is not None and cpu>self.high else 'scale_in' if cpu is not None and cpu<self.low else None
        if direction is None: self.reset(); return None
        if direction != self.direction:
            self.direction=direction; self.count=0; self.first=stamp
        self.count+=1
        return direction if self.count >= (self.high_n if direction=='scale_out' else self.low_n) else None

class Experiment:
    def __init__(self,args,read_sample=sample,act=None):
        self.a=args; self.read_sample=read_sample; self.act=act or self.execute
        self.lock=threading.RLock(); self.stop=threading.Event(); self.notice=threading.Event()
        self.actions=queue.Queue(maxsize=1); self.pending=False; self.generation=0
        self.cool_until=0; self.latest=None; self.version=0; self.handled=0
        self.failure=None; self.confirm=Confirmation(args.high,args.low,args.high_samples,args.low_samples)
        self.path=Path(args.output)/args.run_id; self.path.mkdir(parents=True,exist_ok=False)
        self.file=(self.path/'controller.jsonl').open('w',encoding='utf-8'); self.loglock=threading.Lock()
        self.counts={}; self.started=time.monotonic()
    def log(self,kind,**kw):
        with self.loglock:
            self.counts[kind]=self.counts.get(kind,0)+1
            row=dict(kind=kind,run_id=self.a.run_id,mode=self.a.mode,timestamp=time.time(),monotonic=time.monotonic(),**kw)
            self.file.write(json.dumps(row)+'\n'); self.file.flush()
    def guarded(self,fn):
        try: fn()
        except Exception as e:
            self.failure=repr(e); self.log('thread_error',thread=fn.__name__,error=repr(e)); self.stop.set(); self.notice.set()
    def monitor(self):
        prev=None; seq=0; previous_count=None; previous_generation=None
        while not self.stop.is_set():
            begin=time.monotonic()
            with self.lock: gen=self.generation; pending=self.pending
            try: cpu,names=self.read_sample(); error=None
            except Exception as e: cpu,names,error=None,[],repr(e)
            observed=time.monotonic(); seq+=1
            with self.lock:
                overlap=pending or self.pending or gen!=self.generation
                gap=None if prev is None else observed-prev
                reset=overlap or observed<self.cool_until or cpu is None or (gap is not None and gap>self.a.max_gap) or previous_count!=len(names) or previous_generation!=self.generation
                if reset: self.confirm.reset()
                action=None if reset else self.confirm.update(cpu,observed)
                if action=='scale_out' and len(names)>=3: action=None
                if action=='scale_in' and len(names)<=1: action=None
                old_action=None if self.latest is None else self.latest['action']
                first=self.confirm.first
                if action!=old_action:
                    self.version+=1
                    self.log('condition_changed',version=self.version,action=action,sample_seq=seq,first_threshold_monotonic=first)
                self.latest=dict(action=action,version=self.version,sample_seq=seq,observed=observed,names=names,generation=self.generation,first_threshold_monotonic=first,confirmed_monotonic=(self.latest['confirmed_monotonic'] if action and old_action==action else observed))
                if self.a.mode=='event' and action!=old_action:
                    self.log('notification_sent',version=self.version,action=action)
                    self.notice.set()
                self.log('sample',cpu=cpu,names=names,sample_seq=seq,sampling_seconds=observed-begin,sample_interval_seconds=gap,action_pending=self.pending,overlaps_action=overlap,error=error)
                previous_count=len(names); previous_generation=self.generation
            prev=observed
            self.stop.wait(max(0,self.a.sample_period-(time.monotonic()-begin)))
    def evaluate(self,trigger):
        with self.lock:
            self.log('controller_evaluation',trigger=trigger)
            s=self.latest
            if s is None or not s['action'] or s['version']<=self.handled or self.pending or time.monotonic()<self.cool_until: return
            if time.monotonic()-s['observed']>self.a.max_gap: return
            self.handled=s['version']; self.pending=True; self.generation+=1
            event=dict(s,action_id=f'{self.a.run_id}-{s["version"]}',decision_monotonic=time.monotonic())
            self.log('decision_created',**event,confirmation_to_decision_seconds=event['decision_monotonic']-s['confirmed_monotonic'])
            self.actions.put_nowait(event)
    def control(self):
        tick=self.started
        while not self.stop.is_set():
            if self.a.mode=='polling':
                if self.stop.wait(max(0,tick-time.monotonic())): break
                self.log('state_query'); self.evaluate('poll'); tick+=self.a.poll_period
                if tick<time.monotonic(): tick=time.monotonic()+self.a.poll_period
            else:
                if not self.notice.wait(.2): continue
                self.notice.clear()
                if not self.stop.is_set(): self.log('notification_received'); self.evaluate('event')
    def worker(self):
        while not self.stop.is_set() or not self.actions.empty():
            try: e=self.actions.get(timeout=.2)
            except queue.Empty: continue
            try:
                self.log('action_started',action_id=e['action_id'],action=e['action'],decision_to_start_seconds=time.monotonic()-e['decision_monotonic'])
                start=time.monotonic(); self.act(e)
                self.log('action_finished',action_id=e['action_id'],action=e['action'],success=True,action_seconds=time.monotonic()-start)
            except Exception as exc:
                self.log('action_finished',action_id=e['action_id'],action=e['action'],success=False,error=repr(exc))
                self.failure=repr(exc); self.stop.set(); self.notice.set()
            finally:
                with self.lock:
                    self.cool_until=time.monotonic()+self.a.cooldown; self.pending=False; self.generation+=1
                self.actions.task_done()
    def execute(self,e):
        before=running()
        if before!=e['names']: raise RuntimeError('Container membership changed before action')
        time.sleep(self.a.action_delay)
        if e['action']=='scale_out':
            name=next(n for n in NAMES if n not in before)
            http.post(LB+'/control/drain',json={'backend':URLS[name]},timeout=http.CONTROL_TIMEOUT).raise_for_status()
            docker('start',name)
            admitted=False
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                try:
                    r=http.get(URLS[name]+'/health',timeout=http.HEALTH_TIMEOUT)
                    if r.status_code==200 and r.json().get('status')=='healthy':
                        if not admitted:
                            self.log('application_ready',action_id=e['action_id'],container=name)
                            http.post(LB+'/control/undrain',json={'backend':URLS[name]},timeout=http.CONTROL_TIMEOUT).raise_for_status()
                            admitted=True
                        # Also require LB admission, not only direct application readiness.
                        h=http.get(LB+'/health',timeout=http.CONTROL_TIMEOUT).json()
                        if URLS[name] in h['backends']: break
                except (requests.RequestException,ValueError): pass
                time.sleep(.2)
            else: raise TimeoutError('New container or LB readiness timeout')
            expected=set(before)|{name}
        else:
            name=before[-1]
            r=http.post(LB+'/control/drain',json={'backend':URLS[name]},timeout=http.CONTROL_TIMEOUT);r.raise_for_status()
            deadline=time.monotonic()+35
            while time.monotonic()<deadline:
                h=http.get(LB+'/health',timeout=http.CONTROL_TIMEOUT).json()
                if h['inflight'].get(URLS[name],0)==0: break
                time.sleep(.2)
            else: raise TimeoutError('Drain timeout')
            docker('stop',name)
            expected=set(before)-{name}
            # Remain drained even when the health monitor has a stale/in-flight result.
            self.log('backend_kept_drained',action_id=e['action_id'],container=name)
        if set(running())!=expected: raise RuntimeError('Unexpected final membership')
    def run(self):
        self.log('controller_started',controller_version=http.PROTOCOL,config=vars(self.a),note='Both arms share sampler, confirmation, policy and action worker. Notifications coalesce; controller reads current state.')
        ts=[threading.Thread(target=self.guarded,args=(f,)) for f in (self.monitor,self.control,self.worker)]
        for t in ts:t.start()
        try:
            while not self.stop.wait(.2):
                if time.monotonic()-self.started>=self.a.seconds:break
        except KeyboardInterrupt:pass
        finally:
            self.stop.set();self.notice.set()
            for t in ts:t.join()
            self.log('controller_stopped',error=self.failure)
            self.file.close()
        if self.failure:raise RuntimeError(self.failure)

def main(mode=None):
    p=argparse.ArgumentParser()
    if mode is None:p.add_argument('--mode',choices=['polling','event'],required=True)
    else:p.set_defaults(mode=mode)
    p.add_argument('--run-id',required=True);p.add_argument('--output',default='data/local_comparison')
    for name,default in [('seconds',1200),('sample-period',1),('poll-period',1),('max-gap',10),('cooldown',15),('action-delay',15),('high',70),('low',30)]:p.add_argument('--'+name,type=float,default=default)
    p.add_argument('--high-samples',type=int,default=5);p.add_argument('--low-samples',type=int,default=10)
    a=p.parse_args()
    if any(not math.isfinite(v) for v in vars(a).values() if isinstance(v,float)):p.error('All numeric options must be finite')
    if min(a.seconds,a.sample_period,a.poll_period,a.max_gap,a.high_samples,a.low_samples)<=0 or min(a.cooldown,a.action_delay)<0 or not 0<=a.low<a.high:p.error('Invalid timing or thresholds')
    if Path(a.run_id).name!=a.run_id or a.run_id in ('.','..') or '/' in a.run_id or '\\' in a.run_id:p.error('run-id must be a simple directory name')
    if running()!=['app1']:p.error('Start with app1 running and app2/app3 stopped')
    h=http.wait_ready(LB+'/health', lambda h: h.get('protocol')==http.PROTOCOL and h.get('backends')==[URLS['app1']] and not h.get('draining'))
    if h.get('backends')!=[URLS['app1']] or h.get('draining'):p.error('LB must have only app1 healthy, no drain state')
    Experiment(a).run()
if __name__=='__main__':main()
