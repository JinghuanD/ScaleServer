"""Separate observer. JSON line START/END protocol gives boundary snapshots."""
import argparse
import json
import platform
import sys
import threading
import time
from pathlib import Path
import psutil


def snapshot(process, kind):
    begin = time.time()
    cpu = process.cpu_times()
    mem = process.memory_info()
    threads = process.threads()
    return dict(kind=kind, timestamp=time.time(), monotonic=time.monotonic(),
                collection_started_timestamp=begin, cpu_user_seconds=cpu.user,
                cpu_system_seconds=cpu.system, rss_bytes=mem.rss,
                threads={str(t.id): t.user_time+t.system_time for t in threads})


def summarize(rows, names):
    start, end = rows[0], rows[-1]
    duration = end['monotonic']-start['monotonic']
    cpu = end['cpu_user_seconds']+end['cpu_system_seconds']-start['cpu_user_seconds']-start['cpu_system_seconds']
    integral = sum((b['monotonic']-a['monotonic'])*(a['rss_bytes']+b['rss_bytes'])/2 for a,b in zip(rows,rows[1:]))
    thread_cpu = {}
    for tid, name in names.items():
        if tid in start['threads'] and tid in end['threads']:
            thread_cpu[name] = end['threads'][tid]-start['threads'][tid]
    return dict(measurement_version='resource-v1', duration_seconds=duration,
                started_timestamp=start['timestamp'], finished_timestamp=end['timestamp'],
                process_cpu_seconds=cpu, process_mean_cpu_percent_one_core=100*cpu/duration,
                process_mean_rss_mib=integral/duration/2**20,
                process_sampled_peak_rss_mib=max(r['rss_bytes'] for r in rows)/2**20,
                thread_cpu_seconds=thread_cpu, samples=len(rows),
                max_sample_gap_seconds=max(b['monotonic']-a['monotonic'] for a,b in zip(rows,rows[1:])),
                platform=platform.platform(), python=sys.version, psutil_version=psutil.__version__,
                logical_cpus=psutil.cpu_count(),
                scope='Controller Python process only; observer, workload, LB, Docker CLI and Docker backend excluded. RSS peak is sampled, not a lifetime peak. Thread CPU has OS timer granularity; zero is not proof of zero work.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);p.add_argument('--output',required=True);a=p.parse_args()
    target=psutil.Process(a.pid);folder=Path(a.output)
    rows=[];lock=threading.Lock();stop=threading.Event();failure=[]
    with (folder/'resources.jsonl').open('x',encoding='utf-8') as f:
        def take(kind):
            r=snapshot(target,kind);rows.append(r);f.write(json.dumps(r)+'\n');f.flush();return r
        def sample():
            try:
                while not stop.wait(1):
                    with lock:take('sample')
            except Exception as exc:failure.append(repr(exc));stop.set()
        worker=None
        for line in sys.stdin:
            command=json.loads(line)
            if command['command']=='START':
                with lock:r=take('start')
                worker=threading.Thread(target=sample);worker.start()
                print(json.dumps({'ok':True,'timestamp':r['timestamp']}),flush=True)
            elif command['command']=='END':
                stop.set()
                if worker:worker.join()
                if failure:raise RuntimeError(failure)
                with lock:r=take('end')
                names={}
                for line in (folder/'controller.jsonl').read_text(encoding='utf-8').splitlines():
                    item=json.loads(line)
                    if item['kind']=='thread_registered':names[str(item['native_id'])]=item['thread']
                summary=summarize(rows,names)
                (folder/'resource_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
                print(json.dumps({'ok':True,'timestamp':r['timestamp']}),flush=True)
                break
        stop.set()
        if worker:worker.join()
if __name__=='__main__':main()
