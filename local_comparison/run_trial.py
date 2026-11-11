"""Run one trial. Optional managed LB keeps output away from Windows consoles."""
import argparse
from datetime import datetime
import json
import math
import subprocess
import sys
import time
from pathlib import Path
import local_http as http
import workload_original as workload

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--arm',choices=['polling','event'],required=True)
    p.add_argument('--run-id',required=True)
    p.add_argument('--profile',choices=['stable','step'],required=True)
    p.add_argument('--low-rate',type=float,required=True)
    p.add_argument('--high-rate',type=float)
    p.add_argument('--poll-period',type=float,default=1)
    p.add_argument('--action-delay',type=float,default=15)
    p.add_argument('--manage-lb',action='store_true',help='Start and stop an isolated LB process; port8000 must be free')
    a=p.parse_args()
    if a.profile=='step' and (a.high_rate is None or not math.isfinite(a.high_rate) or a.high_rate<=a.low_rate):p.error('step requires --high-rate greater than low-rate')
    if not math.isfinite(a.low_rate) or a.low_rate<=0:p.error('Invalid low-rate')
    if not math.isfinite(a.poll_period) or a.poll_period<=0 or not math.isfinite(a.action_delay) or a.action_delay<0:p.error('Invalid poll period/action delay')
    if Path(a.run_id).name!=a.run_id or a.run_id in ('.','..') or '\\' in a.run_id:p.error('Invalid run-id')
    pattern=[(a.low_rate,900)] if a.profile=='stable' else [(a.low_rate,180),(a.high_rate,300),(a.low_rate,300)]
    root=Path('data/local_comparison');root.mkdir(parents=True,exist_ok=True)
    requested_id=a.run_id
    if (root/a.run_id).exists() or (root/(a.run_id+'_console.txt')).exists():
        a.run_id += '_retry_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    target=root/a.run_id
    print('Actual run ID:',a.run_id,flush=True)
    cmd=[sys.executable,'-u',str(Path(__file__).with_name('controller.py')),'--mode',a.arm,'--run-id',a.run_id,'--seconds',str(sum(d for _,d in pattern)+140),'--poll-period',str(a.poll_period),'--action-delay',str(a.action_delay)]
    process=lb_process=None;lb_file=None
    console_path=root/(a.run_id+'_console.txt')
    try:
        if a.manage_lb:
            import socket
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1',8000))==0:
                    raise RuntimeError('Port8000 already has a listener. Stop the old LB before --manage-lb; no existing process was killed.')
            lb_file=(root/(a.run_id+'_lb.txt')).open('x',encoding='utf-8')
            lb_process=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('local_load_balancer.py'))],stdout=lb_file,stderr=subprocess.STDOUT)
        http.wait_ready('http://127.0.0.1:8000/health',lambda h:h.get('protocol')==http.PROTOCOL and h.get('backends')==['http://127.0.0.1:5000'] and not h.get('draining'),cancelled=lambda:lb_process is not None and lb_process.poll() is not None)
        with console_path.open('x',encoding='utf-8') as console:
            process=subprocess.Popen(cmd,stdout=console,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+65
            while True:
                if process.poll() is not None:
                    console.flush()
                    raise RuntimeError('Controller preflight failed:\n'+console_path.read_text(encoding='utf-8',errors='replace')[-6000:])
                log=target/'controller.jsonl'
                if log.exists() and '"kind": "controller_started"' in log.read_text():break
                if time.monotonic()>deadline:raise TimeoutError('Controller startup timeout; see '+str(console_path))
                time.sleep(.2)
            print('Controller ready. Waiting20 seconds before workload.',flush=True)
            for _ in range(20):
                if process.poll() is not None:raise RuntimeError('Controller stopped before workload; see '+str(console_path))
                time.sleep(1)
            meta=dict(vars(a),requested_run_id=requested_id,transport_version=http.PROTOCOL,pattern=pattern,workload_started_timestamp=time.time(),controller_command=cmd)
            (target/'manifest.json').write_text(json.dumps(meta,indent=2))
            workload.URL='http://127.0.0.1:8000/work';workload.WORKLOAD_PATTERN=pattern;workload.OUTPUT_DIR=target/'workload'
            workload.main()
            meta['workload_finished_timestamp']=time.time();meta['controller_exited_during_workload']=process.poll() is not None
            (target/'manifest.json').write_text(json.dumps(meta,indent=2))
            print('Waiting for controller tail window (about120 seconds).',flush=True)
            code=process.wait(timeout=210)
            if code or meta['controller_exited_during_workload']:raise RuntimeError('Controller failed; inspect '+str(console_path))
    finally:
        for child in (process,lb_process):
            if child is not None and child.poll() is None:
                child.terminate()
                try:child.wait(timeout=60)
                except subprocess.TimeoutExpired:child.kill();child.wait()
        if lb_file is not None:lb_file.close()
    print('Saved:',target.resolve())
if __name__=='__main__':main()
